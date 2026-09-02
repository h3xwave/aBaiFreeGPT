"""Cloudflare Temp Mailbox Provider (Cloudflare Workers / D1 backed).

Features:
- Multi-domain random rotation (comma/newline separated domain list)
- Dual authentication: User JWT token and Admin direct read fallback (x-admin-auth / Bearer token)
- 6-digit OTP code and verification link extraction
- Automatic temporary mailbox deletion on completion/release
- Direct connection test
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import secrets
import string
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from core.base_mailbox import BaseMailbox, MailboxAccount, _extract_verification_link
from core.domain_imap_mailbox import _normalise_domain

logger = logging.getLogger(__name__)

DEFAULT_CODE_PATTERN = r"(?<!#)(?<!\d)(\d{6})(?!\d)"


def _parse_domains(raw: object) -> list[str]:
    """Parse comma/newline separated domain string or list of domains."""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        cleaned = raw.replace("，", ",").replace("\r", "\n")
        items = [line for chunk in cleaned.split("\n") for line in chunk.split(",")]
    else:
        items = []

    domains: list[str] = []
    seen: set[str] = set()
    for item in items:
        item_str = str(item or "").strip().strip("@").rstrip(".").lower()
        if not item_str:
            continue
        try:
            norm = _normalise_domain(item_str)
            if norm not in seen:
                seen.add(norm)
                domains.append(norm)
        except Exception:
            # Fallback if domain format is slightly non-standard but non-empty
            if "." in item_str and item_str not in seen:
                seen.add(item_str)
                domains.append(item_str)
    return domains


def _positive_number(value: object, default: float, *, minimum: float, maximum: float) -> float:
    try:
        return min(max(float(value or default), minimum), maximum)
    except (TypeError, ValueError):
        return default


def _local_prefix(value: object) -> str:
    prefix = re.sub(r"[^a-z0-9._-]", "", str(value or "reg").strip().lower())
    return (prefix.strip("._-") or "reg")[:24]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


class CloudflareTempMailbox(BaseMailbox):
    """Temporary email provider using Cloudflare Workers / D1 API."""

    def __init__(
        self,
        *,
        api_base: str = "",
        domain: str = "",
        domains: list[str] | str | None = None,
        admin_password: str = "",
        jwt_secret: str = "",
        local_prefix: str = "reg",
        poll_interval: float | str = 3,
        request_timeout: float | str = 15,
        auto_delete: bool = False,
        proxy: str | None = None,
        session: requests.Session | None = None,
    ):
        raw_api = str(api_base or "").strip().rstrip("/")
        if not raw_api:
            raise ValueError("请填写 Cloudflare Mail API 地址，例如：https://apimail.ihxw.eu.org")
        parsed = urlparse(raw_api)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Cloudflare Mail API 地址格式无效，仅支持 http/https")
        self.api_base = raw_api

        # Parse domains
        all_domains = _parse_domains(domains or domain)
        if not all_domains:
            raise ValueError("请填写至少一个有效的 Cloudflare 邮箱域名（支持中英文逗号分隔多域名）")
        self.domains = all_domains

        self.admin_password = str(admin_password or "").strip()
        self.jwt_secret = str(jwt_secret or "").strip()
        self.auth_key = self.admin_password or self.jwt_secret
        if not self.auth_key:
            raise ValueError("请填写 Cloudflare Mail 管理员密码 (admin_password) 或 JWT Secret")

        self.local_prefix = _local_prefix(local_prefix)
        self.poll_interval = _positive_number(poll_interval, 3, minimum=1, maximum=60)
        self.request_timeout = _positive_number(request_timeout, 15, minimum=3, maximum=120)
        self.auto_delete = _truthy(auto_delete)
        self.proxy = proxy

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            if self.proxy:
                self.session.proxies.update({"http": self.proxy, "https": self.proxy})

    @classmethod
    def from_config(cls, config: dict, proxy: str | None = None) -> "CloudflareTempMailbox":
        return cls(
            api_base=config.get("cf_mail_api_base") or config.get("api_base") or config.get("cf_api_url") or "",
            domain=config.get("cf_mail_domain") or config.get("domain") or "",
            domains=config.get("cf_mail_domains") or config.get("domains") or config.get("cf_mail_domain") or "",
            admin_password=config.get("cf_mail_admin_password") or config.get("admin_password") or "",
            jwt_secret=config.get("cf_mail_jwt_secret") or config.get("jwt_secret") or "",
            local_prefix=config.get("cf_mail_local_prefix") or config.get("local_prefix") or "reg",
            poll_interval=config.get("cf_mail_poll_interval") or config.get("poll_interval") or 3,
            request_timeout=config.get("cf_mail_request_timeout") or config.get("request_timeout") or 15,
            auto_delete=_truthy(config.get("cf_mail_auto_delete", config.get("auto_delete", False))),
            proxy=proxy,
        )

    def _get_admin_headers(self) -> dict[str, str]:
        return {
            "x-admin-auth": self.auth_key,
            "Authorization": f"Bearer {self.auth_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _generate_local_name(self) -> str:
        random_suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
        return f"{self.local_prefix}-{random_suffix}"

    def get_email(self) -> MailboxAccount:
        """Create a fresh temporary email address via Cloudflare API."""
        max_retries = 5
        last_error = ""

        for attempt in range(max_retries):
            domain = secrets.choice(self.domains)
            local_name = self._generate_local_name()
            expected_address = f"{local_name}@{domain}"

            payload = {
                "enablePrefix": False,
                "name": local_name,
                "domain": domain,
            }

            try:
                res = self.session.post(
                    f"{self.api_base}/admin/new_address",
                    json=payload,
                    headers=self._get_admin_headers(),
                    timeout=self.request_timeout,
                )

                if res.status_code == 200:
                    data = res.json()
                    jwt_token = data.get("jwt")
                    actual_address = data.get("address") or expected_address
                    logger.info("Cloudflare 邮箱创建成功: %s", actual_address)
                    return MailboxAccount(
                        email=actual_address,
                        account_id=actual_address,
                        extra={
                            "jwt": jwt_token,
                            "domain": domain,
                            "local_name": local_name,
                            "api_base": self.api_base,
                        },
                    )

                if res.status_code == 400 and "already exists" in res.text.lower():
                    logger.warning("Cloudflare 邮箱地址碰撞 (%s)，重试创建...", expected_address)
                    continue

                if res.status_code in (401, 403):
                    raise RuntimeError(f"Cloudflare 管理员密码错误或鉴权失败 (HTTP {res.status_code}): {res.text[:150]}")

                last_error = f"HTTP {res.status_code}: {res.text[:200]}"
            except requests.RequestException as exc:
                last_error = str(exc)

        raise RuntimeError(f"Cloudflare 邮箱创建失败（已重试 {max_retries} 次）: {last_error}")

    def _parse_mail_list(self, data: object) -> list[dict]:
        if isinstance(data, dict):
            for key in ("results", "data", "mails"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        elif isinstance(data, list):
            return data
        return []

    def _fetch_emails(self, account: MailboxAccount) -> list[dict]:
        """Fetch email list using address JWT with fallback to admin direct read."""
        jwt_token = (account.extra or {}).get("jwt")
        target_address = account.email
        local_name = (account.extra or {}).get("local_name") or target_address.split("@")[0]
        domain = (account.extra or {}).get("domain") or (target_address.split("@")[1] if "@" in target_address else "")

        # 1. Try fetching via User JWT if available
        if jwt_token:
            try:
                res = self.session.get(
                    f"{self.api_base}/api/mails",
                    params={"limit": 20, "offset": 0},
                    headers={"Authorization": f"Bearer {jwt_token}", "Accept": "application/json"},
                    timeout=self.request_timeout,
                )
                if res.status_code == 200:
                    return self._parse_mail_list(res.json())
                logger.debug(
                    "Cloudflare JWT 读信状态码异常 (HTTP %s)，尝试回退管理员直读...",
                    res.status_code,
                )
            except Exception as exc:
                logger.debug("Cloudflare JWT 读信异常 (%s)，回退管理员直读...", exc)

        # 2. Admin mode fallback: read directly using admin credentials
        admin_headers = self._get_admin_headers()
        try:
            # First try: /admin/mails?address=xxx
            res = self.session.get(
                f"{self.api_base}/admin/mails",
                params={"address": target_address, "limit": 20, "offset": 0},
                headers=admin_headers,
                timeout=self.request_timeout,
            )
            if res.status_code == 200:
                return self._parse_mail_list(res.json())
        except Exception as exc:
            logger.debug("Cloudflare 管理员按 address 读信异常: %s", exc)

        try:
            # Second try: /admin/mails?name=xxx&domain=yyy
            res = self.session.get(
                f"{self.api_base}/admin/mails",
                params={"name": local_name, "domain": domain, "limit": 20, "offset": 0},
                headers=admin_headers,
                timeout=self.request_timeout,
            )
            if res.status_code == 200:
                return self._parse_mail_list(res.json())
        except Exception as exc:
            logger.debug("Cloudflare 管理员按 name/domain 读信异常: %s", exc)

        return []

    def _fetch_email_detail(self, account: MailboxAccount, mail_id: str | int) -> dict | None:
        """Fetch email detail content by mail ID."""
        msg_id = str(mail_id).strip()
        if msg_id.startswith("/messages/"):
            msg_id = msg_id.split("/")[-1]

        jwt_token = (account.extra or {}).get("jwt")

        def _format_detail(data: object) -> dict | None:
            if not isinstance(data, dict):
                return None
            if "data" in data and isinstance(data["data"], dict):
                data = data["data"]
            html_val = str(data.get("html") or "")
            text_val = str(data.get("text") or "")
            source_val = str(data.get("source") or data.get("raw") or data.get("content") or "")
            data["html"] = html_val or text_val or source_val
            data["text"] = text_val or html_val or source_val
            data["subject"] = str(data.get("subject") or "")
            return data

        # 1. Try User JWT detail endpoint
        if jwt_token:
            try:
                res = self.session.get(
                    f"{self.api_base}/api/mails/{msg_id}",
                    headers={"Authorization": f"Bearer {jwt_token}", "Accept": "application/json"},
                    timeout=self.request_timeout,
                )
                if res.status_code == 200:
                    formatted = _format_detail(res.json())
                    if formatted:
                        return formatted
            except Exception:
                pass

        # 2. Admin detail endpoint fallback
        admin_headers = self._get_admin_headers()
        for endpoint in (f"/admin/mails/{msg_id}", f"/api/mails/{msg_id}"):
            try:
                res = self.session.get(
                    f"{self.api_base}{endpoint}",
                    headers=admin_headers,
                    timeout=self.request_timeout,
                )
                if res.status_code == 200:
                    formatted = _format_detail(res.json())
                    if formatted:
                        return formatted
            except Exception:
                pass

        return None

    def get_current_ids(self, account: MailboxAccount) -> set:
        """Return current message IDs for the account to filter out existing mails."""
        mails = self._fetch_emails(account)
        ids = set()
        for mail in mails:
            mid = mail.get("id")
            if mid is not None:
                ids.add(str(mid))
        return ids

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set | None = None,
        code_pattern: str | None = None,
    ) -> str:
        """Poll for new emails and extract the verification code."""
        seen_ids = set(str(x) for x in (before_ids or set()))
        pattern = code_pattern or DEFAULT_CODE_PATTERN
        deadline = time.time() + max(timeout, 5)

        logger.info("等待 Cloudflare 邮箱 %s 验证码 (超时 %ds)...", account.email, timeout)

        while time.time() < deadline:
            mails = self._fetch_emails(account)
            for mail in mails:
                mail_id = str(mail.get("id") or "")
                if not mail_id or mail_id in seen_ids:
                    continue

                detail = self._fetch_email_detail(account, mail_id) or mail
                subject = str(detail.get("subject") or "")
                text = str(detail.get("text") or "")
                html_body = str(detail.get("html") or "")
                combined = f"{subject}\n{text}\n{html_body}"

                if keyword and keyword.lower() not in combined.lower():
                    continue

                match = re.search(pattern, combined)
                if match:
                    code = match.group(1) if match.groups() else match.group(0)
                    logger.info("Cloudflare 邮箱 %s 获取到验证码: %s", account.email, code)
                    return code

            time.sleep(self.poll_interval)

        raise TimeoutError(f"Cloudflare 邮箱等待验证码超时 ({timeout}秒): {account.email}")

    def wait_for_link(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set | None = None,
    ) -> str:
        """Poll for new emails and extract verification link."""
        seen_ids = set(str(x) for x in (before_ids or set()))
        deadline = time.time() + max(timeout, 5)

        logger.info("等待 Cloudflare 邮箱 %s 验证链接 (超时 %ds)...", account.email, timeout)

        while time.time() < deadline:
            mails = self._fetch_emails(account)
            for mail in mails:
                mail_id = str(mail.get("id") or "")
                if not mail_id or mail_id in seen_ids:
                    continue

                detail = self._fetch_email_detail(account, mail_id) or mail
                subject = str(detail.get("subject") or "")
                text = str(detail.get("text") or "")
                html_body = str(detail.get("html") or "")
                combined = f"{subject}\n{text}\n{html_body}"

                link = _extract_verification_link(combined, keyword=keyword)
                if link:
                    logger.info("Cloudflare 邮箱 %s 获取到验证链接: %s", account.email, link)
                    return link

            time.sleep(self.poll_interval)

        raise TimeoutError(f"Cloudflare 邮箱等待验证链接超时 ({timeout}秒): {account.email}")

    def _delete_address_safe(self, account: MailboxAccount) -> bool:
        jwt_token = (account.extra or {}).get("jwt")
        if not jwt_token:
            return False
        try:
            res = self.session.delete(
                f"{self.api_base}/api/delete_address",
                headers={"Authorization": f"Bearer {jwt_token}"},
                timeout=self.request_timeout,
            )
            return res.status_code == 200
        except Exception:
            return False

    def commit_email(self, account: MailboxAccount) -> bool:
        if self.auto_delete:
            return self._delete_address_safe(account)
        return False

    def release_email(self, account: MailboxAccount) -> bool:
        if self.auto_delete:
            return self._delete_address_safe(account)
        return False

    def test_connection(self) -> None:
        """Verify that the API base and admin auth are working."""
        try:
            res = self.session.get(
                f"{self.api_base}/admin/mails",
                params={"limit": 1, "offset": 0},
                headers=self._get_admin_headers(),
                timeout=self.request_timeout,
            )
            if res.status_code in (200, 400, 404):
                return
            if res.status_code in (401, 403):
                raise RuntimeError(f"Cloudflare 管理员鉴权失败 (HTTP {res.status_code})，请检查管理员密码/Secret")
            raise RuntimeError(f"Cloudflare API 响应异常 (HTTP {res.status_code})")
        except requests.RequestException as exc:
            raise RuntimeError(f"无法连接到 Cloudflare Mail API ({self.api_base}): {exc}") from exc
