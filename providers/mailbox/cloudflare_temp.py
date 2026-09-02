"""Cloudflare Temp Email mailbox provider registration."""

from core.cloudflare_temp_mailbox import CloudflareTempMailbox
from providers.registry import register_provider


register_provider("mailbox", "cloudflare_temp")(CloudflareTempMailbox)
