import logging

import server.agents.execution_agent.tools.gmail as gmail
from server.services.email_validation import EmailVerificationResult


def _verification(
    *,
    suspicious: bool,
    status: str = "VALID",
    suggested_email: str | None = None,
    score: int | None = 100,
) -> EmailVerificationResult:
    return EmailVerificationResult(
        email="recipient@example.com",
        suspicious=suspicious,
        status=status,
        message=(
            f"This recipient might not be reachable. Did you mean {suggested_email}?"
            if suggested_email
            else "Email appears reachable."
        ),
        suggested_email=suggested_email,
        score=score,
    )


def test_gmail_create_draft_valid_recipient_verifies_and_creates_draft(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="openpoke.server")
    execute_calls = []

    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "gmail-user")
    monkeypatch.setattr(gmail, "email_verifier", lambda recipient: _verification(suspicious=False))

    def fake_execute(tool_name, composio_user_id, arguments):
        execute_calls.append((tool_name, composio_user_id, arguments))
        return {"successful": True, "data": {"id": "draft-1"}}

    monkeypatch.setattr(gmail, "_execute", fake_execute)

    result = gmail.gmail_create_draft(
        recipient_email="recipient@example.com",
        subject="Hello",
        body="Body",
    )

    assert result == {"successful": True, "data": {"id": "draft-1"}}
    assert execute_calls == [
        (
            "GMAIL_CREATE_EMAIL_DRAFT",
            "gmail-user",
            {
                "recipient_email": "recipient@example.com",
                "subject": "Hello",
                "body": "Body",
                "cc": None,
                "bcc": None,
                "extra_recipients": None,
                "is_html": None,
                "thread_id": None,
                "attachment": None,
            },
        )
    ]
    assert any("Email verifier check completed" in record.message for record in caplog.records)


def test_gmail_create_draft_suspicious_recipient_blocks_draft(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="openpoke.server")
    execute_called = False

    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "gmail-user")
    monkeypatch.setattr(
        gmail,
        "email_verifier",
        lambda recipient: _verification(
            suspicious=True,
            status="INVALID_DOMAIN",
            suggested_email="recipient@gmail.com",
            score=10,
        ),
    )

    def fake_execute(*args, **kwargs):  # noqa: ARG001
        nonlocal execute_called
        execute_called = True
        return {}

    monkeypatch.setattr(gmail, "_execute", fake_execute)

    result = gmail.gmail_create_draft(
        recipient_email="recipient@gmial.com",
        subject="Hello",
        body="Body",
    )

    assert execute_called is False
    assert result["status"] == "needs_recipient_confirmation"
    assert result["recipient_email"] == "recipient@gmial.com"
    assert result["suggested_email"] == "recipient@gmail.com"
    assert result["verification"] == {
        "status": "INVALID_DOMAIN",
        "score": 10,
        "suspicious": True,
    }
    assert any(
        "Email verifier flagged suspicious recipient; draft creation blocked" in record.message
        for record in caplog.records
    )


def test_gmail_create_draft_unknown_verifier_status_proceeds(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="openpoke.server")
    execute_calls = []

    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "gmail-user")
    monkeypatch.setattr(
        gmail,
        "email_verifier",
        lambda recipient: _verification(suspicious=False, status="UNKNOWN", score=None),
    )

    def fake_execute(tool_name, composio_user_id, arguments):
        execute_calls.append((tool_name, composio_user_id, arguments))
        return {"successful": True}

    monkeypatch.setattr(gmail, "_execute", fake_execute)

    result = gmail.gmail_create_draft(
        recipient_email="recipient@example.com",
        subject="Hello",
        body="Body",
    )

    assert result == {"successful": True}
    assert len(execute_calls) == 1
    assert any("Email verifier unavailable; continuing draft creation" in record.message for record in caplog.records)


def test_gmail_create_draft_skip_verification_uses_override(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="openpoke.server")
    verifier_called = False
    execute_calls = []

    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "gmail-user")

    def fake_verifier(recipient):  # noqa: ARG001
        nonlocal verifier_called
        verifier_called = True
        return _verification(suspicious=True)

    def fake_execute(tool_name, composio_user_id, arguments):
        execute_calls.append((tool_name, composio_user_id, arguments))
        return {"successful": True}

    monkeypatch.setattr(gmail, "email_verifier", fake_verifier)
    monkeypatch.setattr(gmail, "_execute", fake_execute)

    result = gmail.gmail_create_draft(
        recipient_email="recipient@gmial.com",
        subject="Hello",
        body="Body",
        skip_email_verification=True,
    )

    assert result == {"successful": True}
    assert verifier_called is False
    assert len(execute_calls) == 1
    assert any(
        "Email verifier skipped by explicit user confirmation" in record.message
        for record in caplog.records
    )


def test_gmail_create_draft_gmail_not_connected_skips_verifier(monkeypatch):
    verifier_called = False

    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: None)

    def fake_verifier(recipient):  # noqa: ARG001
        nonlocal verifier_called
        verifier_called = True
        return _verification(suspicious=False)

    monkeypatch.setattr(gmail, "email_verifier", fake_verifier)

    result = gmail.gmail_create_draft(
        recipient_email="recipient@example.com",
        subject="Hello",
        body="Body",
    )

    assert verifier_called is False
    assert result == {"error": "Gmail not connected. Please connect Gmail in settings first."}
