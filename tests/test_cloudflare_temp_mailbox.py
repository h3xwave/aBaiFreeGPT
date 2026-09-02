import pytest
from unittest.mock import MagicMock, patch

from core.base_mailbox import MailboxAccount, create_mailbox
from core.cloudflare_temp_mailbox import CloudflareTempMailbox, _parse_domains
from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository
from infrastructure.provider_settings_repository import ProviderSettingsRepository
from providers.registry import create_provider, load_all


def test_parse_domains():
    assert _parse_domains("a.com, b.org，c.net") == ["a.com", "b.org", "c.net"]
    assert _parse_domains(["x.com", "y.org"]) == ["x.com", "y.org"]
    assert _parse_domains("sub.example.com\nanother.domain.com") == ["sub.example.com", "another.domain.com"]
    assert _parse_domains("") == []


def test_cloudflare_temp_mailbox_init_and_config():
    config = {
        "cf_mail_api_base": "https://apimail.test.org",
        "cf_mail_domain": "test1.org, test2.org",
        "cf_mail_admin_password": "secret_admin_pass",
        "cf_mail_local_prefix": "openai",
        "cf_mail_poll_interval": "2",
        "cf_mail_request_timeout": "10",
        "cf_mail_auto_delete": "true",
    }
    mailbox = CloudflareTempMailbox.from_config(config)
    assert mailbox.api_base == "https://apimail.test.org"
    assert mailbox.domains == ["test1.org", "test2.org"]
    assert mailbox.admin_password == "secret_admin_pass"
    assert mailbox.local_prefix == "openai"
    assert mailbox.poll_interval == 2.0
    assert mailbox.request_timeout == 10.0
    assert mailbox.auto_delete is True


def test_get_email_success():
    session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"jwt": "fake_jwt_token_123", "address": "reg-abc12345@test.org"}
    session.post.return_value = mock_res

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        session=session,
    )
    account = mailbox.get_email()

    assert account.email == "reg-abc12345@test.org"
    assert account.extra["jwt"] == "fake_jwt_token_123"
    assert account.extra["domain"] == "test.org"
    session.post.assert_called_once()


def test_get_email_retry_on_collision():
    session = MagicMock()
    res1 = MagicMock()
    res1.status_code = 400
    res1.text = "Address already exists"

    res2 = MagicMock()
    res2.status_code = 200
    res2.json.return_value = {"jwt": "jwt_after_retry", "address": "reg-retry99@test.org"}
    session.post.side_effect = [res1, res2]

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        session=session,
    )
    account = mailbox.get_email()

    assert account.email == "reg-retry99@test.org"
    assert account.extra["jwt"] == "jwt_after_retry"
    assert session.post.call_count == 2


def test_wait_for_code_with_jwt():
    session = MagicMock()
    list_res = MagicMock()
    list_res.status_code = 200
    list_res.json.return_value = {"results": [{"id": "msg_001", "subject": "Your verification code"}]}

    detail_res = MagicMock()
    detail_res.status_code = 200
    detail_res.json.return_value = {"data": {"html": "<p>Your OpenAI code is 849201</p>", "subject": "Verification"}}

    session.get.side_effect = [list_res, detail_res]

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        poll_interval=0.01,
        session=session,
    )
    account = MailboxAccount(
        email="test@test.org",
        extra={"jwt": "valid_user_jwt", "domain": "test.org", "local_name": "test"},
    )

    code = mailbox.wait_for_code(account, timeout=5)
    assert code == "849201"


def test_wait_for_code_admin_fallback():
    session = MagicMock()
    # 1. JWT request fails with 401
    jwt_res = MagicMock()
    jwt_res.status_code = 401

    # 2. Admin address request succeeds
    admin_list_res = MagicMock()
    admin_list_res.status_code = 200
    admin_list_res.json.return_value = {"results": [{"id": "admin_msg_002", "subject": "OpenAI verification"}]}

    # 3. Admin detail request succeeds
    admin_detail_res = MagicMock()
    admin_detail_res.status_code = 200
    admin_detail_res.json.return_value = {"text": "Here is your verification code: 654321"}

    session.get.side_effect = [jwt_res, admin_list_res, admin_detail_res]

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        poll_interval=0.01,
        session=session,
    )
    account = MailboxAccount(
        email="test@test.org",
        extra={"jwt": "expired_jwt", "domain": "test.org", "local_name": "test"},
    )

    code = mailbox.wait_for_code(account, timeout=5)
    assert code == "654321"


def test_wait_for_link_success():
    session = MagicMock()
    list_res = MagicMock()
    list_res.status_code = 200
    list_res.json.return_value = {"results": [{"id": "link_msg", "subject": "Confirm signup"}]}

    detail_res = MagicMock()
    detail_res.status_code = 200
    detail_res.json.return_value = {"html": '<a href="https://auth.openai.com/verify?token=xyz123">Click here to verify</a>'}

    session.get.side_effect = [list_res, detail_res]

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        poll_interval=0.01,
        session=session,
    )
    account = MailboxAccount(
        email="test@test.org",
        extra={"jwt": "user_jwt", "domain": "test.org", "local_name": "test"},
    )

    link = mailbox.wait_for_link(account, keyword="openai", timeout=5)
    assert link == "https://auth.openai.com/verify?token=xyz123"


def test_auto_delete_on_release():
    session = MagicMock()
    del_res = MagicMock()
    del_res.status_code = 200
    del_res.json.return_value = {"success": True}
    session.delete.return_value = del_res

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        auto_delete=True,
        session=session,
    )
    account = MailboxAccount(
        email="test@test.org",
        extra={"jwt": "user_jwt"},
    )

    assert mailbox.release_email(account) is True
    session.delete.assert_called_once()


def test_test_connection():
    session = MagicMock()
    res = MagicMock()
    res.status_code = 200
    res.json.return_value = {"results": []}
    session.get.return_value = res

    mailbox = CloudflareTempMailbox(
        api_base="https://apimail.test.org",
        domains=["test.org"],
        admin_password="admin_secret",
        session=session,
    )
    mailbox.test_connection()
    session.get.assert_called_once()


def test_provider_registration():
    load_all()
    config = {
        "cf_mail_api_base": "https://apimail.test.org",
        "cf_mail_domain": "test.org",
        "cf_mail_admin_password": "secret_admin_pass",
    }
    provider = create_provider("mailbox", "cloudflare_temp", config)
    assert isinstance(provider, CloudflareTempMailbox)


def test_create_mailbox_via_catalog():
    repo = ProviderDefinitionsRepository()
    repo.ensure_seeded()

    settings_repo = ProviderSettingsRepository()
    settings_repo.save(
        setting_id=None,
        provider_type="mailbox",
        provider_key="cloudflare_temp",
        display_name="Cloudflare 临时邮箱",
        auth_mode="admin_auth",
        enabled=True,
        is_default=False,
        config={
            "cf_mail_api_base": "https://apimail.test.org",
            "cf_mail_domain": "domain1.com, domain2.com",
            "cf_mail_local_prefix": "myreg",
        },
        auth={
            "cf_mail_admin_password": "admin_test_pass",
        },
        metadata={},
    )

    mailbox = create_mailbox("cloudflare_temp")
    assert isinstance(mailbox, CloudflareTempMailbox)
    assert mailbox.api_base == "https://apimail.test.org"
    assert mailbox.domains == ["domain1.com", "domain2.com"]
    assert mailbox.admin_password == "admin_test_pass"
    assert mailbox.local_prefix == "myreg"
