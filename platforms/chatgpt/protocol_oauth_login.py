"""ChatGPT OAuth protocol reauthorization engine.

Handles:
- Passwordless email-only flow (OpenAI Email OTP)
- Password-authenticated flow (fallback to Email OTP if triggered)
- 2FA / MFA (TOTP dynamically generated from base32 secret)
- Cloudflare Temp Mailbox automated OTP reading
- Full PKCE token exchange (/oauth/token)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from curl_cffi import requests as cffi_requests
from curl_cffi.requests import Session

from core.base_mailbox import BaseMailbox, MailboxAccount
from platforms.chatgpt.constants import (
    CODEX_CLIENT_ID,
    OPENAI_AUTH,
    OPENAI_API_ENDPOINTS,
    SENTINEL_BASE,
    SENTINEL_FRAME_URL,
    SENTINEL_REQ_URL,
)
from platforms.chatgpt.mfa import totp_code
from platforms.chatgpt.protocol_register import (
    OpenAISentinelClient,
    ProtocolEnvironmentProfile,
    _is_cloudflare_challenge_response,
    _response_error,
    _response_json,
)

logger = logging.getLogger(__name__)

DEFAULT_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_OAUTH_SCOPE = "openid email profile offline_access"


def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:64]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _server_selected_email_otp(page_type: str, page_payload: dict, continue_url: str) -> bool:
    payload = page_payload if isinstance(page_payload, dict) else {}
    mode = str(payload.get("email_verification_mode") or "").strip().lower()
    url_lower = str(continue_url or "").lower()
    return (
        page_type in ("email_otp_verification", "email_otp_send")
        or bool(payload.get("passwordless_login"))
        or "passwordless" in mode
        or "email-verification" in url_lower
        or "email-otp" in url_lower
    )


class ChatGPTProtocolReauthorizer:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        mailbox: BaseMailbox | None = None,
        impersonate: str = "chrome136",
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.proxy = proxy
        self.mailbox = mailbox
        self.impersonate = impersonate
        self.log = log_callback or (lambda msg: logger.info(msg))
        self.profile = ProtocolEnvironmentProfile.generate()
        self.device_id = str(uuid.uuid4())
        self.session = self._create_session()
        self.sentinel = OpenAISentinelClient(
            self.session,
            user_agent=self.profile.user_agent,
            profile=self.profile,
        )

    def _create_session(self) -> Session:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        session = Session(
            proxies=proxies,
            impersonate=self.impersonate,
            verify=False,
            timeout=30,
        )
        session.headers.update(
            {
                "accept-language": self.profile.accept_language,
                "sec-ch-ua": self.profile.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": f'"{self.profile.platform}"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": self.profile.user_agent,
            }
        )
        return session

    def _json_headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": OPENAI_AUTH,
            "Referer": referer,
            "oai-device-id": self.device_id,
        }

    def reauthorize(
        self,
        email: str,
        password: str = "",
        mail_token: str = "",
        totp_secret: str = "",
    ) -> dict[str, Any]:
        """Perform protocol-level Codex OAuth login."""
        self.log(f"[OAuth] 开始执行 {email} 授权流程...")

        # Setup cookies & device id
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(24)

        authorize_params = {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": DEFAULT_OAUTH_REDIRECT_URI,
            "scope": DEFAULT_OAUTH_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "codex_cli_simplified_flow": "true",
            "id_token_add_organizations": "true",
            "prompt": "login",
        }
        authorize_url = f"{OPENAI_AUTH}/oauth/authorize?{urlencode(authorize_params)}"

        # 1. Bootstrap OAuth session
        self.log("[OAuth] 1/6 发起 GET /oauth/authorize 会话初始化...")
        resp = self.session.get(
            authorize_url,
            headers={"Upgrade-Insecure-Requests": "1"},
            allow_redirects=True,
            timeout=30,
        )
        if _is_cloudflare_challenge_response(resp):
            raise RuntimeError("OpenAI 返回 Cloudflare 盾，请更换代理 IP")

        final_url = str(resp.url)
        referer = final_url if final_url.startswith(OPENAI_AUTH) else f"{OPENAI_AUTH}/log-in"

        # 2. Submit Email
        self.log(f"[OAuth] 2/6 提交邮箱账号 {email}...")
        sentinel_headers = self.sentinel.build_headers(self.device_id, "authorize_continue")
        headers = self._json_headers(referer)
        headers.update(sentinel_headers)

        continue_resp = self.session.post(
            f"{OPENAI_AUTH}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )

        if continue_resp.status_code != 200:
            raise RuntimeError(f"提交邮箱失败 (HTTP {continue_resp.status_code}): {continue_resp.text[:180]}")

        continue_data = _response_json(continue_resp)
        page_obj = continue_data.get("page") or {}
        page_type = page_obj.get("type", "")
        page_payload = page_obj.get("payload") or {}
        continue_url = continue_data.get("continue_url", "")

        is_passwordless = _server_selected_email_otp(page_type, page_payload, continue_url)

        # 3. Password Verification (if required)
        if not is_passwordless:
            if not password:
                self.log("[OAuth] 账号需要密码，但未提供密码，尝试请求切换为邮箱免密验证码...")
                # Try triggering email OTP send
                try:
                    otp_req = self.session.post(
                        f"{OPENAI_AUTH}/api/accounts/email-otp/send",
                        json={},
                        headers=self._json_headers(f"{OPENAI_AUTH}/log-in/password"),
                        timeout=30,
                    )
                    if otp_req.status_code == 200:
                        is_passwordless = True
                        self.log("[OAuth] 成功切换为邮箱免密 OTP 验证码模式")
                except Exception:
                    pass

            if not is_passwordless:
                if not password:
                    raise RuntimeError("服务端要求密码验证，但未提供账号密码")

                self.log("[OAuth] 3/6 验证账号密码...")
                sentinel_pwd = self.sentinel.build_headers(self.device_id, "password_verify")
                headers_verify = self._json_headers(f"{OPENAI_AUTH}/log-in/password")
                headers_verify.update(sentinel_pwd)

                pwd_resp = self.session.post(
                    f"{OPENAI_AUTH}/api/accounts/password/verify",
                    json={"password": password},
                    headers=headers_verify,
                    timeout=30,
                    allow_redirects=False,
                )
                if pwd_resp.status_code != 200:
                    raise RuntimeError(f"密码校验失败 (HTTP {pwd_resp.status_code}): {pwd_resp.text[:180]}")

                pwd_data = _response_json(pwd_resp)
                continue_url = pwd_data.get("continue_url", "") or continue_url
                page_obj = pwd_data.get("page") or {}
                page_type = page_obj.get("type", "") or page_type
                page_payload = page_obj.get("payload") or {}

        # 4. Email OTP Verification (if requested)
        need_otp = _server_selected_email_otp(page_type, page_payload, continue_url)
        if need_otp:
            self.log("[OAuth] 4/6 检测到邮箱验证码挑战，开始收取验证码...")
            if not self.mailbox:
                raise RuntimeError("未配置或无法连接邮箱服务，无法收取 6 位验证码")

            account = MailboxAccount(
                email=email,
                account_id=email,
                extra={"jwt": mail_token or None, "domain": email.split("@")[1] if "@" in email else ""},
            )

            # Wait for OTP code via Cloudflare / Mailbox provider
            otp_code = self.mailbox.wait_for_code(account, timeout=120)
            self.log(f"[OAuth] 成功获取到 6 位验证码: {otp_code}，正在验证...")

            headers_otp = self._json_headers(f"{OPENAI_AUTH}/email-verification")
            otp_resp = self.session.post(
                f"{OPENAI_AUTH}/api/accounts/email-otp/validate",
                json={"code": otp_code},
                headers=headers_otp,
                timeout=30,
                allow_redirects=False,
            )
            if otp_resp.status_code != 200:
                raise RuntimeError(f"验证码校验未通过 (HTTP {otp_resp.status_code}): {otp_resp.text[:180]}")

            otp_data = _response_json(otp_resp)
            continue_url = otp_data.get("continue_url", "") or continue_url
            page_obj = otp_data.get("page") or {}
            page_type = page_obj.get("type", "") or page_type
            page_payload = page_obj.get("payload") or {}

        # 5. Handle MFA / 2FA (TOTP)
        if "mfa" in page_type.lower() or "mfa" in continue_url.lower() or page_payload.get("factor_type") == "totp":
            self.log("[OAuth] 5/6 检测到应用 2FA / TOTP 验证...")
            if not totp_secret:
                raise RuntimeError("账号已开启 2FA (TOTP)，但未配置 TOTP Secret")

            mfa_pin = totp_code(totp_secret)
            self.log(f"[OAuth] 动态计算 2FA 动态口令: {mfa_pin}，提交验证...")

            headers_mfa = self._json_headers(f"{OPENAI_AUTH}/mfa")
            mfa_resp = self.session.post(
                f"{OPENAI_AUTH}/api/accounts/mfa/validate",
                json={"code": mfa_pin, "factor_type": "totp"},
                headers=headers_mfa,
                timeout=30,
                allow_redirects=False,
            )
            if mfa_resp.status_code != 200:
                # Some versions post to form-encoded endpoint
                form_resp = self.session.post(
                    f"{OPENAI_AUTH}/mfa",
                    data={"code": mfa_pin},
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": OPENAI_AUTH,
                        "Referer": f"{OPENAI_AUTH}/mfa",
                    },
                    timeout=30,
                    allow_redirects=False,
                )
                if form_resp.status_code >= 400:
                    raise RuntimeError(f"2FA 口令验证失败: {mfa_resp.text[:180]}")
                continue_url = form_resp.headers.get("Location") or continue_url
            else:
                mfa_data = _response_json(mfa_resp)
                continue_url = mfa_data.get("continue_url", "") or continue_url

        # 6. Follow redirect to get callback code
        self.log("[OAuth] 6/6 获取授权回调并交换 Access Token...")
        callback_url = urljoin(OPENAI_AUTH, continue_url) if continue_url else ""
        auth_code = ""

        # Follow redirects until reaching callback URL with code
        current_target = callback_url or authorize_url
        for _ in range(8):
            if "code=" in current_target and ("auth/callback" in current_target or "state=" in current_target):
                parsed = urlparse(current_target)
                qs = parse_qs(parsed.query)
                if "code" in qs:
                    auth_code = qs["code"][0]
                    break

            r_step = self.session.get(current_target, allow_redirects=False, timeout=30)
            loc = r_step.headers.get("Location")
            if not loc:
                break
            current_target = urljoin(OPENAI_AUTH, loc)

        if not auth_code:
            raise RuntimeError("未能从重定向中获取到授权授权码 (Authorization Code)")

        # 7. Exchange Token via POST /oauth/token
        token_payload = {
            "grant_type": "authorization_code",
            "client_id": CODEX_CLIENT_ID,
            "code": auth_code,
            "redirect_uri": DEFAULT_OAUTH_REDIRECT_URI,
            "code_verifier": code_verifier,
        }

        token_resp = self.session.post(
            f"{OPENAI_AUTH}/oauth/token",
            data=token_payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": OPENAI_AUTH,
                "Accept": "application/json",
            },
            timeout=30,
        )

        if token_resp.status_code != 200:
            raise RuntimeError(f"交换 Token 失败 (HTTP {token_resp.status_code}): {token_resp.text[:200]}")

        token_data = _response_json(token_resp)
        if not token_data.get("access_token"):
            raise RuntimeError("交换返回的凭证中缺少 access_token")

        self.log(f"[OAuth] ✅ 账号 {email} 重新授权成功！")
        return token_data
