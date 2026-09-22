import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.evidence import EvidenceRecorder
from src.handoff.notifier import NotificationConfig, send_intervention_notice


def _evidence(tmp_path) -> EvidenceRecorder:
    return EvidenceRecorder(str(tmp_path), "test")


def test_unconfigured_falls_back_to_dry_run(tmp_path):
    evidence = _evidence(tmp_path)
    config = NotificationConfig()  # nothing set -- the offline-by-default case
    assert not config.configured

    result = send_intervention_notice(
        config, evidence, capability_id="lookup_member_balance", step_id="step_3",
        reason="unrecognized page state", current_url="http://127.0.0.1:5055/members/66666",
        screenshot="evidence/.../escalation_step_3.png",
    )

    assert "dry-run" in result
    written = evidence.dir / "notifications" / "step_3.txt"
    assert written.exists()
    content = written.read_text()
    assert "lookup_member_balance" in content
    assert "unrecognized page state" in content
    assert "127.0.0.1:5055/members/66666" in content


def test_configured_sends_via_smtp():
    evidence = MagicMock()
    evidence.log = MagicMock()
    config = NotificationConfig(to_addr="ops@example.com", smtp_host="smtp.example.com")

    with patch("src.handoff.notifier.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

        result = send_intervention_notice(
            config, evidence, capability_id="open_member_sub_account", step_id="step_8",
            reason="irreversible step needs sign-off", current_url="http://127.0.0.1:5055/members/12346",
            screenshot="shot.png",
        )

    assert "sent to ops@example.com" in result
    mock_smtp.starttls.assert_called_once()
    mock_smtp.send_message.assert_called_once()
    sent_msg = mock_smtp.send_message.call_args[0][0]
    assert sent_msg["To"] == "ops@example.com"
    assert "open_member_sub_account" in sent_msg.get_content()
    evidence.log.assert_any_call(
        "notification_sent", channel="email", to="ops@example.com", step_id="step_8"
    )


def test_smtp_failure_falls_back_to_dry_run_without_crashing(tmp_path):
    evidence = _evidence(tmp_path)
    config = NotificationConfig(to_addr="ops@example.com", smtp_host="smtp.example.com")

    with patch("src.handoff.notifier.smtplib.SMTP", side_effect=OSError("connection refused")):
        result = send_intervention_notice(
            config, evidence, capability_id="lookup_member_balance", step_id="step_2",
            reason="test", current_url="http://x", screenshot="shot.png",
        )

    assert "dry-run" in result
    assert (evidence.dir / "notifications" / "step_2.txt").exists()


def test_smtp_password_never_logged(tmp_path):
    evidence = _evidence(tmp_path)
    config = NotificationConfig(
        to_addr="ops@example.com", smtp_host="smtp.example.com",
        smtp_user="bot@example.com", smtp_password="super-secret-value",
    )
    with patch("src.handoff.notifier.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
        send_intervention_notice(
            config, evidence, capability_id="x", step_id="step_1",
            reason="r", current_url="http://x", screenshot="s.png",
        )
    log_text = (evidence.dir / "log.jsonl").read_text()
    assert "super-secret-value" not in log_text


def test_from_env_reads_expected_vars(monkeypatch):
    monkeypatch.setenv("ESCALATION_EMAIL_TO", "5551234567@vtext.com")
    monkeypatch.setenv("ESCALATION_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("ESCALATION_SMTP_PORT", "465")
    monkeypatch.setenv("ESCALATION_SMTP_USE_TLS", "false")
    config = NotificationConfig.from_env()
    assert config.to_addr == "5551234567@vtext.com"
    assert config.smtp_host == "smtp.gmail.com"
    assert config.smtp_port == 465
    assert config.smtp_use_tls is False
    assert config.configured
