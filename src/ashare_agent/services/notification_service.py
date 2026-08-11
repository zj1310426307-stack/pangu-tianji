from __future__ import annotations

from email.message import EmailMessage
import os
import smtplib
from typing import Any, Callable, Mapping

from ..core.contracts import NotificationLevel
from .investment_report_center import InvestmentReportCenter


NOTIFICATION_SERVICE_VERSION = "investment-notification-v1.0.0"


class NotificationService:
    """Deliver audited operating alerts without exposing secrets or trading hooks."""

    def __init__(
        self,
        center: InvestmentReportCenter,
        config: Mapping[str, Any] | None = None,
        smtp_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.center = center
        self.config = dict(config or {})
        self.smtp_factory = smtp_factory or smtplib.SMTP

    def notify(
        self,
        *,
        level: NotificationLevel,
        title: str,
        message: str,
        source_type: str,
        source_id: str,
    ) -> dict[str, Any]:
        """Always publish in-app and optionally send a sanitized email copy."""
        channels = ["in_app"]
        delivery: dict[str, Any] = {
            "in_app": {"state": "delivered"},
            "email": {"state": "disabled"},
            "wechat": {"state": "not_implemented"},
        }
        if bool(self.config.get("email_enabled", False)):
            channels.append("email")
            delivery["email"] = self._send_email(title, message)
        return self.center.save_notification(
            level=level,
            title=title,
            message=message,
            source_type=source_type,
            source_id=source_id,
            channels=channels,
            delivery=delivery,
        )

    def _send_email(self, title: str, message: str) -> dict[str, str]:
        """Read SMTP secrets only from this process environment and never persist them."""
        names = {
            "host": "PANGU_SMTP_HOST",
            "port": "PANGU_SMTP_PORT",
            "username": "PANGU_SMTP_USERNAME",
            "password": "PANGU_SMTP_PASSWORD",
            "sender": "PANGU_SMTP_FROM",
            "recipient": "PANGU_SMTP_TO",
        }
        values = {key: os.getenv(name, "").strip() for key, name in names.items()}
        missing = [names[key] for key in ("host", "sender", "recipient") if not values[key]]
        if missing:
            return {"state": "skipped", "message": "邮件环境变量未完整配置"}
        try:
            port = int(values["port"] or "587")
            email = EmailMessage()
            email["Subject"] = title[:160]
            email["From"] = values["sender"]
            email["To"] = values["recipient"]
            email.set_content(message[:5000])
            with self.smtp_factory(values["host"], port, timeout=10) as client:
                if bool(self.config.get("email_starttls", True)):
                    client.starttls()
                if values["username"]:
                    client.login(values["username"], values["password"])
                client.send_message(email)
            return {"state": "delivered"}
        except Exception as exc:  # SMTP failures must not abort an operating report.
            return {
                "state": "failed",
                "message": f"{type(exc).__name__}: 邮件发送失败",
            }

