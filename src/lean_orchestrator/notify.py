"""Email notifications for events a human should see (adapted from agent-runner).

Delivery uses an SMTP relay when ``LEAN_ORCH_SMTP_HOST`` is set, and otherwise
the local ``sendmail``, ``mail``, or ``mailx`` command. Environment variables:
``LEAN_ORCH_SMTP_HOST``, ``LEAN_ORCH_SMTP_PORT``, ``LEAN_ORCH_SMTP_SECURITY``
(starttls, ssl, none), ``LEAN_ORCH_EMAIL_FROM``, ``LEAN_ORCH_SMTP_USERNAME``,
``LEAN_ORCH_SMTP_PASSWORD``.
"""

from __future__ import annotations

import os
import shutil
import smtplib
import socket
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from .events import Console, EventLog


class NotificationError(RuntimeError):
    """An email could not be delivered."""


def valid_email(value: str) -> bool:
    _, address = parseaddr(value)
    return (
        address == value and address.count("@") == 1 and all(address.split("@"))
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


def smtp_settings() -> tuple[str, int, str, str, str | None, str | None] | None:
    host = os.environ.get("LEAN_ORCH_SMTP_HOST")
    if not host:
        return None
    security = os.environ.get("LEAN_ORCH_SMTP_SECURITY", "starttls").lower()
    if security not in {"starttls", "ssl", "none"}:
        raise NotificationError("LEAN_ORCH_SMTP_SECURITY must be starttls, ssl, or none")
    default_port = {"starttls": 587, "ssl": 465, "none": 25}[security]
    try:
        port = int(os.environ.get("LEAN_ORCH_SMTP_PORT", str(default_port)))
    except ValueError as error:
        raise NotificationError("LEAN_ORCH_SMTP_PORT must be a valid port") from error
    sender = os.environ.get("LEAN_ORCH_EMAIL_FROM", "")
    if not valid_email(sender):
        raise NotificationError("LEAN_ORCH_EMAIL_FROM must be a single email address")
    username = os.environ.get("LEAN_ORCH_SMTP_USERNAME")
    password = os.environ.get("LEAN_ORCH_SMTP_PASSWORD")
    if bool(username) != bool(password):
        raise NotificationError("SMTP username and password must both be set")
    if security == "none" and username:
        raise NotificationError("SMTP authentication requires TLS")
    return host, port, security, sender, username, password


def local_transport() -> tuple[str, str]:
    for command in ("sendmail", "mail", "mailx"):
        path = shutil.which(command)
        if path:
            return command, path
    if Path("/usr/sbin/sendmail").is_file():
        return "sendmail", "/usr/sbin/sendmail"
    raise NotificationError("no mail transport: set LEAN_ORCH_SMTP_HOST or install sendmail, mail, or mailx")


def check_delivery() -> None:
    if smtp_settings() is None:
        local_transport()


def send_email(recipient: str, subject: str, body: str) -> None:
    settings = smtp_settings()
    message = EmailMessage()
    message["From"] = settings[3] if settings else f"lean-orch@{socket.getfqdn()}"
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if settings:
        host, port, security, _, username, password = settings
        try:
            smtp_class = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
            with smtp_class(host, port, timeout=30) as smtp:
                if security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as error:
            raise NotificationError(f"email failed: {error}") from error
        return
    transport, command = local_transport()
    if transport == "sendmail":
        arguments, content = [command, "-t", "-i"], message.as_bytes()
    else:
        arguments, content = [command, "-s", subject, recipient], body.encode()
    try:
        result = subprocess.run(arguments, input=content, capture_output=True, check=False, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise NotificationError("email timed out after 30 seconds") from error
    if result.returncode != 0:
        raise NotificationError(f"{transport} exited with {result.returncode}: "
                                f"{result.stderr.decode(errors='replace').strip()}")


class Notifier:
    """Send configured events by email; failures are logged, never fatal to research."""

    def __init__(self, recipient: str, events: tuple[str, ...], console: Console, log: EventLog,
                 workspace: Path) -> None:
        self.recipient = recipient
        self.events = set(events)
        self.console = console
        self.log = log
        self.workspace = workspace

    def notify(self, event: str, subject: str, body: str) -> None:
        if not self.recipient or event not in self.events:
            return
        try:
            send_email(self.recipient, f"lean-orch: {subject}", f"{body}\n\nWorkspace: {self.workspace}\n")
            self.log.emit("notification_sent", event=event, subject=subject)
        except NotificationError as error:
            self.console.warn(f"could not send notification: {error}")
            self.log.emit("notification_failed", event=event, error=str(error))
