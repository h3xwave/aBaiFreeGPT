"""API endpoints for importing files and running ChatGPT reauthorization tasks."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.base_mailbox import create_mailbox
from core.db import AccountCredentialModel, AccountModel, engine
from core.reauth_parser import ParsedReauthAccount, parse_reauth_text
from platforms.chatgpt.protocol_oauth_login import ChatGPTProtocolReauthorizer
from services.reauth_service import save_reauthorized_account

router = APIRouter(prefix="/chatgpt/reauth", tags=["chatgpt-reauth"])

_reauth_lock = threading.Lock()
_state_lock = threading.Lock()
_task_state = {
    "running": False,
    "total": 0,
    "current": 0,
    "success": 0,
    "fail": 0,
    "logs": [],
    "failed_accounts": [],
}


def _push_log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    with _state_lock:
        _task_state["logs"].append(line)
        if len(_task_state["logs"]) > 500:
            _task_state["logs"].pop(0)


def _lookup_db_totp(email: str) -> str:
    """Check if account already exists in DB and has totp_secret stored."""
    try:
        with Session(engine) as session:
            acc = session.exec(
                select(AccountModel)
                .where(AccountModel.platform == "chatgpt")
                .where(AccountModel.email == email.strip().lower())
            ).first()
            if not acc:
                return ""
            cred = session.exec(
                select(AccountCredentialModel)
                .where(AccountCredentialModel.account_id == acc.id)
                .where(AccountCredentialModel.key == "totp_secret")
            ).first()
            return str(cred.value if cred else "")
    except Exception:
        return ""


def _run_batch_reauth(
    accounts: List[ParsedReauthAccount],
    proxy: str = "",
    interval_seconds: int = 5,
    mailbox_key: str = "cloudflare_temp",
):
    global _task_state
    _task_state["running"] = True
    _task_state["total"] = len(accounts)
    _task_state["current"] = 0
    _task_state["success"] = 0
    _task_state["fail"] = 0
    _task_state["logs"] = []
    _task_state["failed_accounts"] = []

    _push_log(f"开始执行批量重新授权，共 {len(accounts)} 个账号，间隔 {interval_seconds} 秒")

    try:
        mailbox = create_mailbox(mailbox_key)
    except Exception as exc:
        _push_log(f"初始化邮箱驱动 {mailbox_key} 失败: {exc}")
        mailbox = None

    for idx, acc in enumerate(accounts, 1):
        _task_state["current"] = idx
        email = acc.email
        password = acc.password
        totp = acc.totp_secret or _lookup_db_totp(email)

        _push_log(f"[{idx}/{len(accounts)}] 处理账号: {email} (密码: {'有' if password else '免密'}, 2FA: {'有' if totp else '无'})")

        try:
            reauth = ChatGPTProtocolReauthorizer(
                proxy=proxy or None,
                mailbox=mailbox,
                log_callback=_push_log,
            )
            tokens = reauth.reauthorize(
                email=email,
                password=password,
                mail_token=acc.mail_token,
                totp_secret=totp,
            )

            # Persist to database
            save_reauthorized_account(email, tokens, password=password, totp_secret=totp)
            _task_state["success"] += 1
            _push_log(f"[{idx}/{len(accounts)}] ✅ 账号 {email} 入库成功")
        except Exception as exc:
            _task_state["fail"] += 1
            err_msg = str(exc)
            _push_log(f"[{idx}/{len(accounts)}] ❌ 账号 {email} 失败: {err_msg}")
            _task_state["failed_accounts"].append({
                "email": email,
                "reason": err_msg,
                "line": acc.line_number,
            })

        if idx < len(accounts) and interval_seconds > 0:
            _push_log(f"休眠 {interval_seconds} 秒防风控...")
            time.sleep(interval_seconds)

    _push_log(f"批量重新授权任务完成！成功: {_task_state['success']}, 失败: {_task_state['fail']}")
    _task_state["running"] = False


class StartReauthRequest(BaseModel):
    text: Optional[str] = Field("", description="批量账号文本内容")
    proxy: Optional[str] = Field("", description="可选指定代理")
    interval_seconds: Optional[int] = Field(5, ge=0, le=300, description="账号间隔秒数")
    mailbox_key: Optional[str] = Field("cloudflare_temp", description="邮箱提供商，默认 cloudflare_temp")


@router.post("/preview")
async def preview_accounts(
    text: str = Query(default="", description="直接传入的文本内容"),
    file: Optional[UploadFile] = File(default=None),
):
    """解析上传的 TXT/CSV 文件或直接文本，返回预览数据。"""
    raw_content = ""
    if file:
        content_bytes = await file.read()
        for enc in ("utf-8-sig", "gb18030", "utf-8"):
            try:
                raw_content = content_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    elif text:
        raw_content = text
    else:
        raise HTTPException(status_code=400, detail="请上传文件或提供文本内容")

    accounts, warnings = parse_reauth_text(raw_content)
    preview_items = [
        {
            "email": a.email,
            "has_password": bool(a.password),
            "has_mail_token": bool(a.mail_token),
            "has_totp": bool(a.totp_secret or _lookup_db_totp(a.email)),
            "line": a.line_number,
        }
        for a in accounts[:50]
    ]

    return {
        "total": len(accounts),
        "accounts": preview_items,
        "warnings": warnings,
    }


@router.post("/start")
async def start_reauth_task(
    payload: StartReauthRequest,
    file: Optional[UploadFile] = File(default=None),
):
    """启动批量重新授权后台任务。"""
    with _reauth_lock:
        if _task_state["running"]:
            raise HTTPException(status_code=409, detail="已有重新授权任务在运行中，请等待完成")

    raw_content = payload.text or ""
    if file:
        content_bytes = await file.read()
        for enc in ("utf-8-sig", "gb18030", "utf-8"):
            try:
                raw_content = content_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

    if not raw_content.strip():
        raise HTTPException(status_code=400, detail="没有可执行的账号内容")

    accounts, _ = parse_reauth_text(raw_content)
    if not accounts:
        raise HTTPException(status_code=400, detail="未解析到任何有效格式的邮箱账号")

    thread = threading.Thread(
        target=_run_batch_reauth,
        kwargs={
            "accounts": accounts,
            "proxy": payload.proxy or "",
            "interval_seconds": payload.interval_seconds or 5,
            "mailbox_key": payload.mailbox_key or "cloudflare_temp",
        },
        daemon=True,
    )
    thread.start()

    return {"status": "started", "total": len(accounts)}


@router.get("/status")
def get_reauth_status():
    """获取当前重新授权任务的执行进度与实时日志。"""
    with _state_lock:
        return {
            "running": _task_state["running"],
            "total": _task_state["total"],
            "current": _task_state["current"],
            "success": _task_state["success"],
            "fail": _task_state["fail"],
            "logs": list(_task_state["logs"]),
            "failed_accounts": list(_task_state["failed_accounts"]),
        }
