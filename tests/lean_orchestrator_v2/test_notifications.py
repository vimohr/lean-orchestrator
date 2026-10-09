import json

import pytest

from lean_orchestrator import notify
from lean_orchestrator_v2 import cli
from lean_orchestrator_v2.config import load_config
from lean_orchestrator_v2.demo import PACKET, DemoBackend
from lean_orchestrator_v2.engine import Harness


SEND_EMAIL = notify.send_email


@pytest.fixture
def session(tmp_path, monkeypatch):
    store = cli.init(tmp_path / "campaign")
    path = store.root / "harness.toml"
    path.write_text(path.read_text().replace('sandbox = "docker"', 'sandbox = "local"')
                    .replace('email = ""', 'email = "saved@example.org"'))
    campaign = store.load()
    store.add(campaign, PACKET, store.root)
    store.save(campaign)
    backend = DemoBackend()
    monkeypatch.setattr(cli, "Harness", lambda store, config: Harness(
        store, config, backends={role: backend for role in ("solver", "coordinator", "reviewer")}))
    messages = []
    monkeypatch.setattr(notify, "send_email", lambda recipient, subject, body: messages.append((recipient, subject, body)))
    return store, backend, messages


def test_run_email_override_is_temporary_and_completion_contains_usage(session):
    store, _, messages = session
    assert cli.main(["-w", str(store.root), "run", "--email", "override@example.org"]) == 0
    assert len(messages) == 2 and {item[0] for item in messages} == {"override@example.org"}
    assert "result awaiting human review" in messages[0][1]
    assert messages[1][1] == "lean-orch-v2: run finished"
    assert "round limit reached" in messages[1][2] and '"known_tokens": 0' in messages[1][2]
    assert load_config(store.root).notify.email == "saved@example.org"
    assert (store.root / "REPORT.md").exists()


def test_existing_configuration_without_notify_keeps_email_disabled(session):
    store, _, messages = session
    path = store.root / "harness.toml"
    text = path.read_text()
    path.write_text(text[:text.index("[notify]")] + text[text.index("[solver]"):])
    assert load_config(store.root).notify.email == ""
    assert cli.main(["-w", str(store.root), "run"]) == 0 and messages == []


def test_event_filter_can_send_only_run_completion(session):
    store, _, messages = session
    path = store.root / "harness.toml"
    path.write_text(path.read_text().replace(
        'events = ["resolution_claimed", "approval_required", "run_stopped", "run_failed"]',
        'events = ["run_stopped"]'))
    assert cli.main(["-w", str(store.root), "run"]) == 0
    assert len(messages) == 1 and messages[0][1] == "lean-orch-v2: run finished"


@pytest.mark.parametrize("replacement", ['email = "invalid"', 'email = "Name <me@example.org>"',
                                          'email = "valid@example.org"\nevents = ["typo"]'])
def test_invalid_notification_settings_are_rejected_before_model_calls(session, replacement):
    store, backend, messages = session
    path = store.root / "harness.toml"
    text = path.read_text()
    path.write_text(text[:text.index("[notify]")] + "[notify]\n" + replacement + "\n\n" + text[text.index("[solver]"):])
    assert cli.main(["-w", str(store.root), "run"]) == 1
    assert backend.requests == [] and messages == []


def test_invalid_email_override_does_not_start_models(session):
    store, backend, messages = session
    assert cli.main(["-w", str(store.root), "run", "--email", "invalid"]) == 1
    assert backend.requests == [] and messages == []


def test_stop_requested_sends_summary_without_starting_models(session):
    store, backend, messages = session
    assert cli.main(["-w", str(store.root), "stop"]) == 0
    assert cli.main(["-w", str(store.root), "run"]) == 0
    assert backend.requests == [] and len(messages) == 1
    assert "stop requested" in messages[0][2] and "Rounds advanced: 0" in messages[0][2]


def test_no_saved_plan_sends_completion_without_starting_models(session):
    store, backend, messages = session
    assert cli.main(["-w", str(store.root), "run", "--no-plan"]) == 0
    assert backend.requests == [] and len(messages) == 1
    assert "no saved allocations" in messages[0][2]


@pytest.mark.parametrize("interrupted", [False, True])
def test_failed_and_interrupted_run_notify_and_keep_exit_status(session, monkeypatch, interrupted):
    store, _, messages = session
    def fail(self, proposals=None):
        if interrupted:
            raise KeyboardInterrupt
        raise RuntimeError("coordinator unavailable")
    monkeypatch.setattr(Harness, "propose", fail)
    assert cli.main(["-w", str(store.root), "run"]) == (130 if interrupted else 1)
    assert len(messages) == 1
    assert messages[0][1] == ("lean-orch-v2: run finished" if interrupted else "lean-orch-v2: run failed")
    events = [json.loads(line) for line in (store.root / "notifications.jsonl").read_text().splitlines()]
    assert events[0]["event"] == ("run_stopped" if interrupted else "run_failed")


def test_cli_notifications_use_smtp_transport_with_tls_and_authentication(session, monkeypatch):
    store, _, _ = session
    sent, operations = [], []
    class SMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("smtp.example.org", 587, 30)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def starttls(self, context):
            operations.append("tls")
        def login(self, username, password):
            assert (username, password) == ("test-user", "test-password")
            operations.append("login")
        def send_message(self, message):
            sent.append(message)
            operations.append("send")
    monkeypatch.setattr(notify, "send_email", SEND_EMAIL)
    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    monkeypatch.setenv("LEAN_ORCH_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("LEAN_ORCH_EMAIL_FROM", "bot@example.org")
    monkeypatch.setenv("LEAN_ORCH_SMTP_USERNAME", "test-user")
    monkeypatch.setenv("LEAN_ORCH_SMTP_PASSWORD", "test-password")
    monkeypatch.setenv("LEAN_ORCH_SMTP_SECURITY", "starttls")
    monkeypatch.setenv("LEAN_ORCH_SMTP_PORT", "587")
    assert cli.main(["-w", str(store.root), "run"]) == 0
    assert operations == ["tls", "login", "send"] * 2
    assert len(sent) == 2 and all(message["To"] == "saved@example.org" for message in sent)
    assert all(message["From"] == "bot@example.org" and message["Subject"].startswith("lean-orch-v2:") for message in sent)


def test_cli_notifications_use_local_sendmail_when_smtp_is_unset(session, monkeypatch):
    store, _, _ = session
    sent = []
    monkeypatch.setattr(notify, "send_email", SEND_EMAIL)
    monkeypatch.delenv("LEAN_ORCH_SMTP_HOST", raising=False)
    monkeypatch.setattr(notify.shutil, "which", lambda command: "/test/sendmail" if command == "sendmail" else None)
    def send(arguments, *, input, capture_output, check, timeout):
        assert arguments == ["/test/sendmail", "-t", "-i"] and timeout == 30
        sent.append(input)
        return type("Result", (), {"returncode": 0})()
    monkeypatch.setattr(notify.subprocess, "run", send)
    assert cli.main(["-w", str(store.root), "run"]) == 0
    assert len(sent) == 2 and all(b"To: saved@example.org" in content for content in sent)
