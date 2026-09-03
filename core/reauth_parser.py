"""Account file and text parser for reauthorization.

Aligned with the multi-column format from upstream:
    email
    email----password
    email----password----email_password
    email----password----email_password----mail_token
    email----password----email_password----mail_token----totp_secret
    email----password----totp_secret
    email----------------totp_secret

Also supports CSV (comma-separated), Tab-separated, and space-separated lines.
Includes smart heuristics to identify base32 TOTP secrets.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_BASE32_RE = re.compile(r"^[A-Z2-7]{16,64}$")
MAX_IMPORT_ACCOUNTS = 5000


@dataclass
class ParsedReauthAccount:
    email: str
    password: str = ""
    email_password: str = ""
    mail_token: str = ""
    totp_secret: str = ""
    line_number: int = 0


def _is_probable_totp_secret(text: str) -> bool:
    clean = text.strip().replace(" ", "").upper()
    return bool(_BASE32_RE.fullmatch(clean)) and len(clean) in (16, 26, 32, 64)


def _split_account_line(line: str) -> list[str]:
    if "----" in line:
        return [part.strip() for part in line.split("----")]
    if "\t" in line:
        return [part.strip() for part in line.split("\t")]
    if "," in line:
        try:
            return [part.strip() for part in next(csv.reader([line]))]
        except Exception:
            return [part.strip() for part in line.split(",")]
    return line.split(None)


def parse_reauth_text(text: str) -> Tuple[List[ParsedReauthAccount], List[str]]:
    """Parse reauthorization accounts from raw text content."""
    accounts: List[ParsedReauthAccount] = []
    warnings: List[str] = []
    seen: set[str] = set()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().strip("\ufeff")
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        parts = _split_account_line(line)
        email = parts[0].strip() if parts else ""
        if not _EMAIL_RE.match(email):
            warnings.append(f"第 {line_number} 行：邮箱格式不正确 ({email})，已跳过")
            continue

        key = email.casefold()
        if key in seen:
            warnings.append(f"第 {line_number} 行：邮箱重复 ({email})，已跳过")
            continue
        seen.add(key)

        password = ""
        email_password = ""
        mail_token = ""
        totp_secret = ""

        if len(parts) == 2:
            val = parts[1].strip()
            # 如果第 2 列是 16/32 位 Base32 秘钥且全大写字母数字，可能是仅邮箱+2FA
            if _is_probable_totp_secret(val):
                totp_secret = val
            else:
                password = val
        elif len(parts) == 3:
            # 常见格式：email----password----totp_secret 或 email----password----email_pwd
            password = parts[1].strip()
            part3 = parts[2].strip()
            if _is_probable_totp_secret(part3):
                totp_secret = part3
            else:
                email_password = part3
        elif len(parts) == 4:
            password = parts[1].strip()
            email_password = parts[2].strip()
            part4 = parts[3].strip()
            if _is_probable_totp_secret(part4):
                totp_secret = part4
            else:
                mail_token = part4
        elif len(parts) >= 5:
            password = parts[1].strip()
            email_password = parts[2].strip()
            mail_token = parts[3].strip()
            totp_secret = parts[4].strip()

        accounts.append(
            ParsedReauthAccount(
                email=email,
                password=password,
                email_password=email_password,
                mail_token=mail_token,
                totp_secret=totp_secret,
                line_number=line_number,
            )
        )

        if len(accounts) >= MAX_IMPORT_ACCOUNTS:
            warnings.append(f"已达到单次最大导入限制 {MAX_IMPORT_ACCOUNTS} 个账号，剩余行被忽略")
            break

    return accounts, warnings
