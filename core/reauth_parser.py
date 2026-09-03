"""Account file and text parser for reauthorization.

Aligned with the multi-column format from upstream:
    email
    email----password
    email----password----email_password
    email----password----email_password----mail_token
    email----password----email_password----mail_token----totp_secret
    email----------------totp_secret

Also supports CSV (comma-separated), Tab-separated, and space-separated lines.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_IMPORT_ACCOUNTS = 5000


@dataclass
class ParsedReauthAccount:
    email: str
    password: str = ""
    email_password: str = ""
    mail_token: str = ""
    totp_secret: str = ""
    line_number: int = 0


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

        password = parts[1].strip() if len(parts) > 1 else ""
        email_password = parts[2].strip() if len(parts) > 2 else ""
        mail_token = parts[3].strip() if len(parts) > 3 else ""
        totp_secret = parts[4].strip() if len(parts) > 4 else ""

        # Special case: 2 parts where part 2 looks like a 32-char base32 TOTP secret rather than password
        if len(parts) == 2 and not totp_secret:
            potential_secret = parts[1].strip().replace(" ", "").upper()
            if len(potential_secret) in (16, 26, 32) and re.fullmatch(r"[A-Z2-7]+", potential_secret):
                # Could be used as either or both
                pass

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
