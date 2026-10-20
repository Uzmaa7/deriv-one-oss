"""Where operational alerts go.

Two things raise alerts: a background job that failed, and a policy condition
worth a human's attention. Neither should know anything about the destination.

Sending an alert must never raise. A failure to deliver an alert is logged and
swallowed — an unreachable alert destination is not a reason to fail the request
that noticed the problem.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    title: str
    body: str = ""
    severity: Severity = Severity.WARNING
    #: Structured detail — ids, counts, durations. Must not contain secrets.
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "body": self.body,
            "severity": self.severity.value,
            "context": self.context,
        }


class AlertSink(ABC):
    """A destination for operational alerts."""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver ``alert``. Must not raise."""


class LogAlertSink(AlertSink):
    """Alerts to the application log. The default, and always available."""

    _LEVELS = {
        Severity.INFO: logging.INFO,
        Severity.WARNING: logging.WARNING,
        Severity.ERROR: logging.ERROR,
        Severity.CRITICAL: logging.CRITICAL,
    }

    def send(self, alert: Alert) -> None:
        try:
            logger.log(
                self._LEVELS.get(alert.severity, logging.WARNING),
                "ALERT %s: %s %s",
                alert.title,
                alert.body,
                json.dumps(alert.context, default=str) if alert.context else "",
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to log alert")


class WebhookAlertSink(AlertSink):
    """Alerts as an HTTP POST to ``ALERT_WEBHOOK_URL``.

    The body is the alert as JSON. Most chat and paging systems accept a JSON
    webhook directly or behind a one-line transform, so this covers the common
    case without the platform knowing which one is on the other end.
    """

    def __init__(self, url: str | None = None, timeout: float = 5.0) -> None:
        self._url = (url or os.environ.get("ALERT_WEBHOOK_URL") or "").strip()
        self._timeout = timeout
        self._fallback = LogAlertSink()
        if not self._url:
            logger.warning("ALERT_WEBHOOK_URL is not set; alerts will only be logged")

    def send(self, alert: Alert) -> None:
        self._fallback.send(alert)
        if not self._url:
            return
        try:
            import httpx

            response = httpx.post(
                self._url, json=alert.to_dict(), timeout=self._timeout
            )
            if response.status_code >= 400:
                logger.warning(
                    "alert webhook returned %s", response.status_code
                )
        except Exception:
            logger.exception("failed to deliver alert to webhook")
