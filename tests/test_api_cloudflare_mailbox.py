import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from main import app
from infrastructure.provider_definitions_repository import ProviderDefinitionsRepository
from infrastructure.provider_settings_repository import ProviderSettingsRepository


@pytest.fixture
def client():
    return TestClient(app)


def test_list_messages_missing_email(client):
    res = client.get("/api/cloudflare-mailbox/messages")
    assert res.status_code == 422


def test_list_messages_invalid_email(client):
    res = client.get("/api/cloudflare-mailbox/messages?email=invalidemail")
    assert res.status_code == 400


@patch("api.cloudflare_mailbox._get_mailbox_instance")
def test_list_messages_success(mock_get_instance, client):
    mock_mailbox = MagicMock()
    mock_mailbox.fetch_messages_for_email.return_value = [
        {"id": "cf_001", "subject": "Test Mail 1", "from": "sender@test.org"},
        {"id": "cf_002", "subject": "Test Mail 2", "from": "sender2@test.org"},
    ]
    mock_get_instance.return_value = mock_mailbox

    res = client.get("/api/cloudflare-mailbox/messages?email=user@test.org")
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "user@test.org"
    assert data["count"] == 2
    assert len(data["messages"]) == 2


@patch("api.cloudflare_mailbox._get_mailbox_instance")
def test_get_message_detail_with_code_extraction(mock_get_instance, client):
    mock_mailbox = MagicMock()
    mock_mailbox.get_message_detail.return_value = {
        "id": "cf_msg_99",
        "subject": "OpenAI Verification Code",
        "html": "<p>Your 6-digit code is <strong>987654</strong>.</p><a href='https://auth.openai.com/verify?token=123'>Verify</a>",
        "text": "Your code is 987654",
    }
    mock_get_instance.return_value = mock_mailbox

    res = client.get("/api/cloudflare-mailbox/messages/cf_msg_99?email=user@test.org")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "cf_msg_99"
    assert data["extracted_code"] == "987654"
    assert data["extracted_link"] == "https://auth.openai.com/verify?token=123"


@patch("api.cloudflare_mailbox._get_mailbox_instance")
def test_fetch_code_endpoint(mock_get_instance, client):
    mock_mailbox = MagicMock()
    mock_mailbox.wait_for_code.return_value = "123456"
    mock_get_instance.return_value = mock_mailbox

    res = client.post(
        "/api/cloudflare-mailbox/fetch-code",
        json={"email": "user@test.org", "keyword": "openai", "timeout": 10},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "user@test.org"
    assert data["code"] == "123456"
    assert data["status"] == "success"
