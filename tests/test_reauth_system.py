import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from core.reauth_parser import parse_reauth_text
from main import app
from services.reauth_service import save_reauthorized_account


@pytest.fixture
def client():
    return TestClient(app)


def test_parse_reauth_text():
    text = """
    # Comments
    user1@test.org
    user2@test.org----pwd123
    user3@test.org----pwd456----mailpwd----tokenxyz----JBSWY3DPEHPK3PXP
    invalid_email
    """
    accounts, warnings = parse_reauth_text(text)
    assert len(accounts) == 3
    assert accounts[0].email == "user1@test.org"
    assert accounts[0].password == ""
    assert accounts[1].email == "user2@test.org"
    assert accounts[1].password == "pwd123"
    assert accounts[2].email == "user3@test.org"
    assert accounts[2].totp_secret == "JBSWY3DPEHPK3PXP"
    assert len(warnings) == 1
    assert "invalid_email" in warnings[0]


def test_preview_endpoint(client):
    res = client.post(
        "/api/chatgpt/reauth/preview",
        params={"text": "abc@example.com\ndef@example.com----pass123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["accounts"][0]["email"] == "abc@example.com"
    assert data["accounts"][0]["has_password"] is False
    assert data["accounts"][1]["has_password"] is True


def test_save_reauthorized_account():
    fake_tokens = {
        "access_token": "mock_access_token_123",
        "refresh_token": "mock_refresh_token_456",
        "id_token": "mock_id_token_789",
    }
    acc = save_reauthorized_account(
        email="reauth_test@example.com",
        tokens=fake_tokens,
        password="testpassword",
        totp_secret="MOCKTOTP",
    )
    assert acc.email == "reauth_test@example.com"
    assert acc.platform == "chatgpt"
