import httpx

from server.services.email_validation.verifier import email_verifier


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _patch_rapid(monkeypatch, payload):
    calls = []

    def fake_get(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return _FakeResponse(payload)

    monkeypatch.setattr("server.services.email_validation.verifier.httpx.get", fake_get)
    return calls


def test_email_verifier_valid_email_not_suspicious(monkeypatch):
    calls = _patch_rapid(
        monkeypatch,
        {
            "email": "user@gmail.com",
            "validations": {
                "syntax": True,
                "domain_exists": True,
                "mx_records": True,
                "mailbox_exists": True,
                "is_disposable": False,
                "is_role_based": False,
            },
            "score": 100,
            "status": "VALID",
        },
    )

    result = email_verifier(" user@gmail.com ")

    assert result.email == "user@gmail.com"
    assert result.suspicious is False
    assert result.status == "VALID"
    assert result.score == 100
    assert result.suggested_email is None
    assert calls and "email=user%40gmail.com" in calls[0]["url"]


def test_email_verifier_invalid_format_is_suspicious(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "invalid-email",
            "validations": {"syntax": False},
            "score": 0,
            "status": "INVALID_FORMAT",
        },
    )

    result = email_verifier("invalid-email")

    assert result.suspicious is True
    assert result.status == "INVALID_FORMAT"
    assert result.suggested_email is None


def test_email_verifier_typo_suggestion_is_returned(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "user@gmial.com",
            "validations": {
                "syntax": True,
                "domain_exists": False,
                "mx_records": False,
            },
            "score": 10,
            "status": "INVALID_DOMAIN",
            "typoSuggestion": "user@gmail.com",
        },
    )

    result = email_verifier("user@gmial.com")

    assert result.suspicious is True
    assert result.status == "INVALID_DOMAIN"
    assert result.suggested_email == "user@gmail.com"
    assert "user@gmail.com" in result.message


def test_email_verifier_invalid_domain_without_suggestion(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "user@nonexistent.example",
            "validations": {
                "syntax": True,
                "domain_exists": False,
                "mx_records": False,
            },
            "score": 40,
            "status": "INVALID_DOMAIN",
        },
    )

    result = email_verifier("user@nonexistent.example")

    assert result.suspicious is True
    assert result.suggested_email is None
    assert "Are you sure" in result.message


def test_email_verifier_no_mx_records_is_suspicious(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "user@example.com",
            "validations": {
                "syntax": True,
                "domain_exists": True,
                "mx_records": False,
            },
            "score": 40,
            "status": "NO_MX_RECORDS",
        },
    )

    result = email_verifier("user@example.com")

    assert result.suspicious is True
    assert result.status == "NO_MX_RECORDS"


def test_email_verifier_disposable_is_suspicious(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "user@tempmail.com",
            "validations": {
                "syntax": True,
                "domain_exists": True,
                "mx_records": True,
                "is_disposable": True,
            },
            "score": 60,
            "status": "DISPOSABLE",
        },
    )

    result = email_verifier("user@tempmail.com")

    assert result.suspicious is True
    assert result.status == "DISPOSABLE"


def test_email_verifier_low_score_is_suspicious(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "admin@company.com",
            "validations": {
                "syntax": True,
                "domain_exists": True,
                "mx_records": True,
                "is_role_based": True,
            },
            "score": 40,
            "status": "PROBABLY_VALID",
        },
    )

    result = email_verifier("admin@company.com")

    assert result.suspicious is True
    assert result.status == "PROBABLY_VALID"


def test_email_verifier_role_based_valid_email_not_suspicious(monkeypatch):
    _patch_rapid(
        monkeypatch,
        {
            "email": "admin@company.com",
            "validations": {
                "syntax": True,
                "domain_exists": True,
                "mx_records": True,
                "is_role_based": True,
            },
            "score": 80,
            "status": "PROBABLY_VALID",
        },
    )

    result = email_verifier("admin@company.com")

    assert result.suspicious is False
    assert result.status == "PROBABLY_VALID"


def test_email_verifier_empty_email_is_suspicious_without_api_call(monkeypatch):
    def fake_get(url, timeout):  # noqa: ARG001
        raise AssertionError("API should not be called for empty email")

    monkeypatch.setattr("server.services.email_validation.verifier.httpx.get", fake_get)

    result = email_verifier("   ")

    assert result.email == ""
    assert result.suspicious is True
    assert result.status == "INVALID_FORMAT"


def test_email_verifier_api_failure_returns_unknown_not_suspicious(monkeypatch):
    def fake_get(url, timeout):  # noqa: ARG001
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("server.services.email_validation.verifier.httpx.get", fake_get)

    result = email_verifier("user@gmail.com")

    assert result.suspicious is False
    assert result.status == "UNKNOWN"
    assert result.message == "Email verification unavailable."


def test_email_verifier_malformed_payload_returns_unknown_not_suspicious(monkeypatch):
    _patch_rapid(monkeypatch, {"email": "user@gmail.com"})

    result = email_verifier("user@gmail.com")

    assert result.suspicious is False
    assert result.status == "UNKNOWN"
    assert result.raw == {"email": "user@gmail.com"}
