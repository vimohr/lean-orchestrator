from __future__ import annotations

import pytest
from support import quiet_console

from lean_orchestrator import notify
from lean_orchestrator.events import EventLog
from lean_orchestrator.notify import NotificationError, Notifier, smtp_settings, valid_email


def test_email_validation():
    assert valid_email("someone@example.org")
    for value in ("", "a@b@c", "name <a@b.c>", "a@", "a b@c.d"):
        assert not valid_email(value)


def test_smtp_settings(monkeypatch):
    monkeypatch.delenv("LEAN_ORCH_SMTP_HOST", raising=False)
    assert smtp_settings() is None
    monkeypatch.setenv("LEAN_ORCH_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("LEAN_ORCH_EMAIL_FROM", "bot@example.org")
    assert smtp_settings() == ("smtp.example.org", 587, "starttls", "bot@example.org", None, None)
    monkeypatch.setenv("LEAN_ORCH_SMTP_SECURITY", "none")
    monkeypatch.setenv("LEAN_ORCH_SMTP_USERNAME", "u")
    monkeypatch.setenv("LEAN_ORCH_SMTP_PASSWORD", "p")
    with pytest.raises(NotificationError, match="requires TLS"):
        smtp_settings()


def test_notifier_filters_events_and_survives_delivery_failures(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda recipient, subject, body: sent.append(subject))
    log = EventLog(tmp_path / "events.jsonl")
    notifier = Notifier("me@example.org", ("resolution_claimed",), quiet_console(), log, tmp_path)
    notifier.notify("resolution_claimed", "claimed", "body")
    notifier.notify("run_stopped", "ignored", "body")
    assert sent == ["lean-orch: claimed"]

    def failing(recipient, subject, body):
        raise NotificationError("no transport")

    monkeypatch.setattr(notify, "send_email", failing)
    notifier.notify("resolution_claimed", "again", "body")
    assert [event["kind"] for event in log.read()] == ["notification_sent", "notification_failed"]
    Notifier("", ("resolution_claimed",), quiet_console(), log, tmp_path).notify("resolution_claimed", "x", "y")
