"""Service for persisting reauthorized account tokens into SQLite and JSON files."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from core.account_graph import sync_account_graph
from core.db import (
    AccountCredentialModel,
    AccountModel,
    AccountOverviewModel,
    engine,
)

logger = logging.getLogger(__name__)

CODEX_TOKENS_DIR = Path(__file__).resolve().parent.parent / "data" / "codex_tokens"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def save_reauthorized_account(
    email: str,
    tokens: Dict[str, Any],
    password: str = "",
    totp_secret: str = "",
) -> AccountModel:
    """Save or update reauthorized ChatGPT account in the database and disk JSON."""
    email_clean = email.strip().lower()
    access_token = str(tokens.get("access_token") or "")
    refresh_token = str(tokens.get("refresh_token") or "")
    id_token = str(tokens.get("id_token") or "")

    # 1. Save auth JSON file to data/codex_tokens/<email>.json
    try:
        CODEX_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
        token_file = CODEX_TOKENS_DIR / f"{email_clean}.json"
        auth_payload = {
            "type": "codex",
            "email": email_clean,
            "tokens": tokens,
            "updated_at": _utcnow().isoformat(),
        }
        token_file.write_text(json.dumps(auth_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("保存 Token JSON 文件失败: %s", exc)

    # 2. Persist to SQLite accounts database
    with Session(engine) as session:
        account = session.exec(
            select(AccountModel)
            .where(AccountModel.platform == "chatgpt")
            .where(AccountModel.email == email_clean)
        ).first()

        if not account:
            account = AccountModel(
                platform="chatgpt",
                email=email_clean,
                password=password or "(passwordless)",
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(account)
            session.commit()
            session.refresh(account)
        else:
            if password:
                account.password = password
            account.updated_at = _utcnow()
            session.add(account)
            session.commit()
            session.refresh(account)

        account_id = int(account.id or 0)

        # 3. Update or create AccountOverviewModel
        overview = session.get(AccountOverviewModel, account_id)
        if not overview:
            overview = AccountOverviewModel(
                account_id=account_id,
                lifecycle_status="active",
                validity_status="valid",
                plan_state="free",
                display_status="active",
                remote_email=email_clean,
                checked_at=_utcnow(),
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            overview.set_summary({
                "source": "reauth",
                "login_mode": "passwordless" if not password else "password",
                "reauthorized_at": _utcnow().isoformat(),
            })
            session.add(overview)
        else:
            overview.lifecycle_status = "active"
            overview.validity_status = "valid"
            overview.display_status = "active"
            overview.checked_at = _utcnow()
            overview.updated_at = _utcnow()
            summary = overview.get_summary()
            summary["reauthorized_at"] = _utcnow().isoformat()
            summary["status"] = "active"
            overview.set_summary(summary)
            session.add(overview)

        # 4. Upsert credentials (access_token, refresh_token, id_token, totp_secret)
        def _upsert_cred(key: str, value: str, cred_type: str = "token", is_prim: bool = False):
            if not value:
                return
            cred = session.exec(
                select(AccountCredentialModel)
                .where(AccountCredentialModel.account_id == account_id)
                .where(AccountCredentialModel.key == key)
            ).first()
            if not cred:
                cred = AccountCredentialModel(
                    account_id=account_id,
                    scope="platform",
                    provider_name="chatgpt",
                    credential_type=cred_type,
                    key=key,
                    value=value,
                    is_primary=is_prim,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            else:
                cred.value = value
                cred.updated_at = _utcnow()
                if is_prim:
                    cred.is_primary = True
            session.add(cred)

        _upsert_cred("access_token", access_token, "token", is_prim=True)
        _upsert_cred("refresh_token", refresh_token, "token")
        _upsert_cred("id_token", id_token, "token")
        if totp_secret:
            _upsert_cred("totp_secret", totp_secret, "secret")

        session.commit()

        # 5. Sync account graph to keep cache & relations fresh
        try:
            sync_account_graph(session, account)
        except Exception as sync_exc:
            logger.debug("同步 account graph 异常: %s", sync_exc)

        return account
