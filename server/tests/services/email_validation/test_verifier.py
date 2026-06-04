from types import SimpleNamespace

import server.services.email_validation.verifier as verifier
from server.services.email_validation.verifier import email_verifier, extract_email_addresses


def _settings(enabled: bool = True):
    return SimpleNamespace(
        email_verifier_enabled=enabled,
        email_verifier_model=None,
        interaction_agent_model="test-model",
        openrouter_api_key="test-key",
    )


def _patch_settings(monkeypatch, *, enabled: bool = True):
    monkeypatch.setattr(verifier, "get_settings", lambda: _settings(enabled=enabled))


def test_extract_email_addresses_dedupes_preserving_order():
    result = extract_email_addresses(
        "Email user@example.com, user@example.com and second@test.org."
    )

    assert result == ["user@example.com", "second@test.org"]


def test_email_verifier_malformed_email_is_suspicious_without_llm(monkeypatch):
    _patch_settings(monkeypatch)

    def fake_judge(email):  # noqa: ARG001
        raise AssertionError("LLM judge should not run for malformed email")

    monkeypatch.setattr(verifier, "_judge_email_with_llm", fake_judge)

    result = email_verifier("invalid-email")

    assert result.suspicious is True
    assert result.status == "MALFORMED"
    assert result.message == "This does not look like a well-formed email address."


def test_email_verifier_disabled_returns_not_suspicious(monkeypatch):
    _patch_settings(monkeypatch, enabled=False)

    result = email_verifier("user@example.com")

    assert result.suspicious is False
    assert result.status == "DISABLED"


def test_email_verifier_llm_looks_ok(monkeypatch):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        verifier,
        "_judge_email_with_llm",
        lambda email: {
            "status": "LOOKS_OK",
            "suspicious": False,
            "suggested_email": None,
            "message": "No obvious issue found with this email address.",
        },
    )

    result = email_verifier("user@gmail.com")

    assert result.suspicious is False
    assert result.status == "LOOKS_OK"
    assert result.suggested_email is None


def test_email_verifier_llm_suspicious_typo_with_suggestion(monkeypatch):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        verifier,
        "_judge_email_with_llm",
        lambda email: {
            "status": "SUSPICIOUS_TYPO",
            "suspicious": True,
            "suggested_email": "user@gmail.com",
            "message": "This looks like it may be a typo. Did you mean user@gmail.com?",
        },
    )

    result = email_verifier("user@gmial.com")

    assert result.suspicious is True
    assert result.status == "SUSPICIOUS_TYPO"
    assert result.suggested_email == "user@gmail.com"
    assert "user@gmail.com" in result.message


def test_email_verifier_malformed_llm_payload_returns_unknown(monkeypatch):
    _patch_settings(monkeypatch)
    monkeypatch.setattr(
        verifier,
        "_judge_email_with_llm",
        lambda email: {"status": "LOOKS_OK", "suspicious": "false"},
    )

    result = email_verifier("user@gmail.com")

    assert result.suspicious is False
    assert result.status == "UNKNOWN"
    assert result.raw == {"status": "LOOKS_OK", "suspicious": "false"}


def test_email_verifier_llm_failure_returns_unknown(monkeypatch):
    _patch_settings(monkeypatch)

    def fake_judge(email):  # noqa: ARG001
        raise RuntimeError("llm failed")

    monkeypatch.setattr(verifier, "_judge_email_with_llm", fake_judge)

    result = email_verifier("user@gmail.com")

    assert result.suspicious is False
    assert result.status == "UNKNOWN"
    assert result.message == "Email verification unavailable."
