from unittest.mock import patch

import pytest

from seo_workbook_agent.chat_auth import ChatAuthError, verify_chat_bearer_token

AUDIENCE = "https://agent.example.com/chat"


def test_missing_header_raises():
    with pytest.raises(ChatAuthError):
        verify_chat_bearer_token(None, audience=AUDIENCE)


def test_non_bearer_header_raises():
    with pytest.raises(ChatAuthError):
        verify_chat_bearer_token("Basic abc123", audience=AUDIENCE)


def test_valid_token_from_chat_service_account_passes():
    with patch("seo_workbook_agent.chat_auth.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {"email": "chat@system.gserviceaccount.com", "aud": AUDIENCE}
        verify_chat_bearer_token("Bearer faketoken", audience=AUDIENCE)
        mock_verify.assert_called_once()
        assert mock_verify.call_args.kwargs["audience"] == AUDIENCE


def test_wrong_issuer_email_raises():
    with patch("seo_workbook_agent.chat_auth.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {"email": "someone-else@example.com"}
        with pytest.raises(ChatAuthError):
            verify_chat_bearer_token("Bearer faketoken", audience=AUDIENCE)


def test_verification_failure_raises():
    with patch(
        "seo_workbook_agent.chat_auth.id_token.verify_oauth2_token",
        side_effect=ValueError("bad token"),
    ):
        with pytest.raises(ChatAuthError):
            verify_chat_bearer_token("Bearer faketoken", audience=AUDIENCE)
