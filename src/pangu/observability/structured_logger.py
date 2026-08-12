"""JSON logging with a fixed, redacted event contract."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .event_store import EngineeringEventStore


_SENSITIVE_KEY = re.compile(r"api[_-]?key|password|secret|token|authorization|credential", re.I)
_SECRET_VALUE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def _sanitize(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("***REDACTED***", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "pangu_event", None)
        if not isinstance(payload, dict):
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "module": record.name,
                "event": "log_message",
                "run_id": None,
                "user_id": None,
                "trace_id": str(uuid4()),
                "message": record.getMessage(),
                "extra": {},
            }
        return json.dumps(_sanitize(payload), ensure_ascii=False, sort_keys=True)


def configure_structured_logging(log_path: Path, level: str = "INFO") -> logging.Logger:
    """Configure one idempotent rotating JSONL engineering logger."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pangu.engineering")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    if not any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.resolve() for handler in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    return logger


class PanguLogger:
    """Emit a structured event to JSONL and the append-only audit store."""

    def __init__(self, logger: logging.Logger, store: EngineeringEventStore | None = None):
        self.logger = logger
        self.store = store

    def event(
        self,
        level: str,
        module: str,
        event: str,
        message: str,
        *,
        run_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create, sanitize, log and optionally persist one event."""
        payload = _sanitize(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": str(level).upper(),
                "module": module,
                "event": event,
                "run_id": run_id,
                "user_id": user_id,
                "trace_id": trace_id or str(uuid4()),
                "message": message,
                "extra": extra or {},
            }
        )
        numeric = getattr(logging, payload["level"], logging.INFO)
        self.logger.log(numeric, payload["message"], extra={"pangu_event": payload})
        if self.store is not None:
            self.store.append_event(payload)
        return payload
