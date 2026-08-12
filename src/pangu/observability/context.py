"""Context-variable based correlation propagation without global mutable state."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Iterator
from uuid import uuid4

from .contracts import TelemetryContext, validate_identifier


_CURRENT: ContextVar[TelemetryContext | None] = ContextVar("pangu_telemetry_context", default=None)


def new_id(prefix: str) -> str:
    """Create one bounded, non-secret correlation identifier."""
    return f"{prefix}-{uuid4().hex}"


def current_context() -> TelemetryContext | None:
    """Read the context for the current task/thread."""
    return _CURRENT.get()


def create_context(**identifiers: str | None) -> TelemetryContext:
    """Create a validated root context, inheriting no mutable state."""
    trace_id = identifiers.pop("trace_id", None) or new_id("trace")
    safe = {key: validate_identifier(value, key) if value else None for key, value in identifiers.items()}
    return TelemetryContext(trace_id=validate_identifier(trace_id, "trace_id"), **safe)


@contextmanager
def telemetry_context(context: TelemetryContext) -> Iterator[TelemetryContext]:
    """Bind and reliably reset one context for a bounded operation."""
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


@contextmanager
def child_context(**identifiers: str | None) -> Iterator[TelemetryContext]:
    """Derive a child context while retaining root correlation identifiers."""
    parent = current_context() or create_context()
    updates = {
        "parent_span_id": parent.span_id,
        "span_id": identifiers.pop("span_id", None) or new_id("span"),
    }
    updates.update({key: value for key, value in identifiers.items() if value is not None})
    child = replace(parent, **updates)
    with telemetry_context(child):
        yield child
