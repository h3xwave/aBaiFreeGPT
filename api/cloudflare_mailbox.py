"""API routes for inspecting Cloudflare Temp Mailbox messages."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from core.base_mailbox import MailboxAccount, _extract_verification_link
from core.cloudflare_temp_mailbox import CloudflareTempMailbox, DEFAULT_CODE_PATTERN
from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository
from infrastructure.provider_settings_repository import ProviderSettingsRepository

router = APIRouter(prefix="/cloudflare-mailbox", tags=["cloudflare-mailbox"])

settings_repo = ProviderSettingsRepository()
definitions_repo = ProviderDefinitionsRepository()


def _get_mailbox_instance(
    api_base_override: str = "",
    admin_password_override: str = "",
    domain_override: str = "",
) -> CloudflareTempMailbox:
    """Resolve runtime settings for cloudflare_temp and instantiate mailbox."""
    resolved = settings_repo.resolve_runtime_settings("mailbox", "cloudflare_temp")

    api_base = (api_base_override or resolved.get("cf_mail_api_base") or "").strip()
    admin_password = (admin_password_override or resolved.get("cf_mail_admin_password") or "").strip()
    jwt_secret = str(resolved.get("cf_mail_jwt_secret") or "").strip()
    domains = (domain_override or resolved.get("cf_mail_domain") or "example.com").strip()

    if not api_base:
        raise HTTPException(
            status_code=400,
            detail="尚未配置 Cloudflare Mail API 地址，请在设置中配置或在请求中传入 api_base",
        )
    if not admin_password and not jwt_secret:
        raise HTTPException(
            status_code=400,
            detail="尚未配置 Cloudflare Mail 管理员密码 (admin_password)，请在设置中配置",
        )

    return CloudflareTempMailbox(
        api_base=api_base,
        domains=domains,
        admin_password=admin_password,
        jwt_secret=jwt_secret,
        poll_interval=float(resolved.get("cf_mail_poll_interval") or 2),
        request_timeout=float(resolved.get("cf_mail_request_timeout") or 15),
    )


class FetchCodeRequest(BaseModel):
    email: str = Field(..., description="目标邮箱地址")
    jwt: Optional[str] = Field(None, description="可选专属地址 JWT")
    keyword: Optional[str] = Field("", description="关键词过滤（如 openai）")
    timeout: Optional[int] = Field(30, ge=3, le=180, description="等待超时秒数")


@router.get("/messages")
async def list_messages(
    email: str = Query(default="", description="可选邮箱地址，留空则获取全部邮件"),
    limit: int = Query(default=10, ge=1, le=100, description="读取条数，默认10"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    api_base: str = Query(default="", description="可选覆盖 API 地址"),
    admin_password: str = Query(default="", description="可选覆盖管理员密码"),
):
    """查询 Cloudflare 邮箱的收件列表（若未输入邮箱则查询全站所有最新信件）。"""
    decoded_email = unquote(email.strip()) if email else ""
    if decoded_email and "@" not in decoded_email:
        raise HTTPException(status_code=400, detail="邮箱地址格式无效")

    domain_override = decoded_email.split("@")[1] if ("@" in decoded_email) else ""
    mailbox = _get_mailbox_instance(
        api_base_override=api_base,
        admin_password_override=admin_password,
        domain_override=domain_override,
    )

    try:
        messages = await run_in_threadpool(
            mailbox.fetch_messages_for_email,
            email=decoded_email,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 Cloudflare 邮件列表失败: {exc}") from exc

    return {
        "email": decoded_email,
        "count": len(messages),
        "limit": limit,
        "offset": offset,
        "messages": messages,
    }


@router.get("/messages/{message_id}")
async def get_message_detail(
    message_id: str,
    email: str = Query(default="", description="可选邮箱地址"),
    jwt: str = Query(default="", description="可选邮箱地址专属 JWT"),
    api_base: str = Query(default="", description="可选覆盖 API 地址"),
    admin_password: str = Query(default="", description="可选覆盖管理员密码"),
):
    """获取指定邮件详情，并自动提取 6 位验证码与验证链接。"""
    clean_id = unquote(message_id.strip())
    mailbox = _get_mailbox_instance(
        api_base_override=api_base,
        admin_password_override=admin_password,
    )

    try:
        detail = await run_in_threadpool(
            mailbox.get_message_detail,
            mail_id=clean_id,
            jwt_token=jwt or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取邮件详情失败: {exc}") from exc

    if not detail:
        raise HTTPException(status_code=404, detail=f"未找到 ID 为 {clean_id} 的邮件")

    subject = str(detail.get("subject") or "")
    text = str(detail.get("text") or "")
    html_content = str(detail.get("html") or "")
    combined = f"{subject}\n{text}\n{html_content}"

    # 自动解析提取 6 位验证码
    code_match = re.search(DEFAULT_CODE_PATTERN, combined)
    extracted_code = code_match.group(1) if code_match else None

    # 自动解析提取验证链接
    extracted_link = _extract_verification_link(combined)

    return {
        "id": clean_id,
        "email": email,
        "subject": subject,
        "text": text,
        "html": html_content,
        "extracted_code": extracted_code,
        "extracted_link": extracted_link,
        "raw": detail,
    }


@router.post("/fetch-code")
async def fetch_code(payload: FetchCodeRequest):
    """轮询等待并获取指定邮箱最新的验证码。"""
    decoded_email = unquote(payload.email.strip())
    if "@" not in decoded_email:
        raise HTTPException(status_code=400, detail="邮箱地址格式无效")

    domain = decoded_email.split("@")[1]
    mailbox = _get_mailbox_instance(domain_override=domain)

    account = MailboxAccount(
        email=decoded_email,
        account_id=decoded_email,
        extra={"jwt": payload.jwt, "domain": domain},
    )

    try:
        code = await run_in_threadpool(
            mailbox.wait_for_code,
            account=account,
            keyword=payload.keyword or "",
            timeout=payload.timeout or 30,
        )
        return {
            "email": decoded_email,
            "code": code,
            "status": "success",
        }
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取验证码失败: {exc}") from exc
