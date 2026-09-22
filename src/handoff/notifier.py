"""
Sends the actual "someone needs to know about this" notification when
`HandoffController.request_intervention` fires -- an email (or, via the
same mechanism, a text message; see below), not just a banner printed to
whichever terminal happens to be watching the automation run.

The assignment is explicit that escalation means routing an intervention
request "to a human operator," not just logging one -- a print statement to
stdout only reaches a human already staring at that terminal. This closes
that gap with the smallest real mechanism that doesn't require a paid
third-party API or embedding a credential in the repo:

  - Email, via plain SMTP (smtplib) -- works with any provider, including a
    free personal account with an app password.
  - Text message, via the same code path: most US carriers expose an
    email-to-SMS gateway (e.g. 5551234567@vtext.com for Verizon,
    @txt.att.net for AT&T, @tmomail.net for T-Mobile). Setting
    ESCALATION_EMAIL_TO to that address sends a real text with zero
    additional code -- no Twilio account, no per-message cost. See README
    "Configuration".

No SMTP configured (the default, so the demo runs with no external
services)?  Falls back to writing the exact email that *would* have been
sent to evidence/<run>/notifications/<step_id>.txt, and that fallback path
is also what an SMTP send failure lands on -- a notification failure must
never crash the run it's trying to report on.
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional


@dataclass
class NotificationConfig:
    to_addr: Optional[str] = None
    from_addr: str = "automation@sterling-core.local"
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True

    @classmethod
    def from_env(cls) -> "NotificationConfig":
        return cls(
            to_addr=os.environ.get("ESCALATION_EMAIL_TO"),
            from_addr=os.environ.get("ESCALATION_EMAIL_FROM", "automation@sterling-core.local"),
            smtp_host=os.environ.get("ESCALATION_SMTP_HOST"),
            smtp_port=int(os.environ.get("ESCALATION_SMTP_PORT", "587")),
            smtp_user=os.environ.get("ESCALATION_SMTP_USER"),
            smtp_password=os.environ.get("ESCALATION_SMTP_PASSWORD"),
            smtp_use_tls=os.environ.get("ESCALATION_SMTP_USE_TLS", "true").strip().lower() != "false",
        )

    @property
    def configured(self) -> bool:
        return bool(self.to_addr and self.smtp_host)


def send_intervention_notice(
    config: NotificationConfig, evidence, *, capability_id: str, step_id: str,
    reason: str, current_url: str, screenshot: str,
) -> str:
    """Returns a short human-readable description of what happened, logged
    by the caller alongside the rest of the intervention-request evidence."""
    subject = f"[Sterling Core Automation] Human intervention needed: {capability_id} / {step_id}"
    body = (
        f"Capability : {capability_id}\n"
        f"Step       : {step_id}\n"
        f"Reason     : {reason}\n"
        f"Current URL: {current_url}\n"
        f"Screenshot : {screenshot}\n\n"
        "To take over: attach to the operator console this run is waiting on (the terminal running "
        "`python -m src.cli replay ... --handoff interactive`), or resolve it there directly.\n"
    )

    if not config.configured:
        return _write_dry_run(evidence, step_id, subject, body)

    try:
        _send_email(config, subject, body)
        evidence.log("notification_sent", channel="email", to=config.to_addr, step_id=step_id)
        return f"email sent to {config.to_addr}"
    except Exception as e:  # noqa: BLE001 -- a notification failure must not crash the run it's reporting on
        evidence.log("notification_failed", channel="email", error=str(e), step_id=step_id)
        return _write_dry_run(evidence, step_id, subject, body, note=f"SMTP send failed, wrote this instead: {e}")


def _send_email(config: NotificationConfig, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.from_addr
    msg["To"] = config.to_addr
    msg.set_content(body)
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10) as smtp:
        if config.smtp_use_tls:
            smtp.starttls()
        if config.smtp_user and config.smtp_password:
            smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(msg)


def _write_dry_run(evidence, step_id: str, subject: str, body: str, note: Optional[str] = None) -> str:
    notif_dir = evidence.dir / "notifications"
    notif_dir.mkdir(parents=True, exist_ok=True)
    path = notif_dir / f"{step_id}.txt"
    content = f"Subject: {subject}\n\n{body}"
    if note:
        content += f"\n[{note}]\n"
    path.write_text(content)
    evidence.log("notification_dry_run", step_id=step_id, path=str(path), reason="smtp_failed" if note else "not_configured")
    return f"dry-run (no ESCALATION_SMTP_HOST/ESCALATION_EMAIL_TO set) -- written to {path}"
