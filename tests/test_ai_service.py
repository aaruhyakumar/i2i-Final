"""Unit tests for Claude Vision AI service abstraction and error handling."""

import os
import pytest
from unittest.mock import patch, MagicMock
from backend.services.claude_service import ClaudeService, DEFAULT_CLAUDE_MODEL


def test_claude_service_unconfigured():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        service = ClaudeService(api_key="")
        assert not service.is_configured()
        res = service.extract_from_images(b"fake_p1", "image/jpeg", b"fake_p2", "image/jpeg")
        assert res["success"] is False
        assert res["code"] == "AI_NOT_CONFIGURED"
        assert res["manual_entry_allowed"] is True


def test_claude_service_configured_flag():
    service = ClaudeService(api_key="sk-ant-test-12345678901234567890")
    assert service.is_configured()


def test_claude_service_default_model_and_override():
    service = ClaudeService(api_key="sk-ant-test-12345678901234567890")
    assert DEFAULT_CLAUDE_MODEL in service.provider_name()

    custom_service = ClaudeService(api_key="sk-ant-test-12345678901234567890", model="claude-3-7-sonnet-20250219")
    assert "claude-3-7-sonnet-20250219" in custom_service.provider_name()


@patch("requests.post")
def test_claude_service_auth_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Invalid API key"
    mock_post.return_value = mock_resp

    service = ClaudeService(api_key="sk-ant-invalid_key_testing")
    res = service.extract_from_images(b"img1", "image/jpeg", b"img2", "image/jpeg")
    assert res["success"] is False
    assert res["code"] == "AUTH_ERROR"
    assert res["manual_entry_allowed"] is True


@patch("requests.post")
def test_claude_service_successful_extraction(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [
            {
                "type": "text",
                "text": '{"person_name": "Rohan M", "age": "42", "gender": "Male", "height": "168 cm", "weight": "68 kg", "blood_pressure": "118/78", "examination_details": "Normal", "referral_details": null, "missing_fields": [], "uncertain_fields": [], "extraction_notes": "Clean scan"}'
            }
        ],
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 400
        }
    }
    mock_post.return_value = mock_resp

    service = ClaudeService(api_key="sk-ant-valid_testing_key")
    res = service.extract_from_images(b"img1", "image/jpeg", b"img2", "image/jpeg")
    assert res["success"] is True
    assert res["data"]["person_name"] == "Rohan M"
    assert res["data"]["age"] == "42"
    assert res["data"]["gender"] == "Male"

    # Verify Anthropic request headers and payload structure
    assert mock_post.called
    call_args, call_kwargs = mock_post.call_args
    headers = call_kwargs.get("headers", {})
    payload = call_kwargs.get("json", {})
    assert headers.get("x-api-key") == "sk-ant-valid_testing_key"
    assert headers.get("anthropic-version") == "2023-06-01"
    assert payload.get("model") == DEFAULT_CLAUDE_MODEL
    messages = payload.get("messages", [])
    assert len(messages) == 1
    content_blocks = messages[0].get("content", [])
    assert any(b.get("type") == "image" for b in content_blocks)
    assert any(b.get("type") == "text" for b in content_blocks)
