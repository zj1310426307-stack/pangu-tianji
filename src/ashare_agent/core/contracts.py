from dataclasses import dataclass
from enum import Enum
from typing import Any


class RuntimeState(str, Enum):
    """Describe only the lifecycle of a research run."""

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelState(str, Enum):
    """Describe model connectivity independently from trading safety."""

    NOT_CONFIGURED = "not_configured"
    CHECKING = "checking"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True)
class CompletedRunSnapshot:
    """Expose a read-only completed run to model providers."""

    run_id: str
    completed_at: str
    data_source: str
    metrics: dict[str, Any]
    strategy_summary: dict[str, Any]


@dataclass(frozen=True)
class ModelExplanation:
    """Contain model-authored research text with explicit execution isolation."""

    summary: str
    risks: list[str]
    model: str
    generated_at: str
    used_for_execution: bool = False


class CopilotAgentType(str, Enum):
    """Identify one bounded AI Investment Copilot responsibility."""

    RESEARCH = "research"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    REVIEW = "review"
    COACH = "coach"


class CopilotReportType(str, Enum):
    """Enumerate the report products supported by the five Copilot agents."""

    MORNING_REPORT = "morning_report"
    CLOSE_REVIEW = "close_review"
    STOCK_ANALYSIS = "stock_analysis"
    PORTFOLIO_ANALYSIS = "portfolio_analysis"
    RISK_ALERT = "risk_alert"
    COACH_REVIEW = "coach_review"


class InvestmentReportType(str, Enum):
    """Identify the four non-executable Daily Investment OS products."""

    MORNING_REPORT = "morning_report"
    INTRADAY_MONITOR = "intraday_monitor"
    CLOSING_REVIEW = "closing_review"
    WEEKLY_REPORT = "weekly_report"


class NotificationLevel(str, Enum):
    """Classify in-app and optional email operating notifications."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ScheduledJobDefinition:
    """Describe one scheduler-owned job without granting execution capabilities."""

    job_name: str
    report_type: InvestmentReportType
    label: str
    schedule_kind: str
    time_of_day: str | None = None
    weekday: int | None = None
    interval_minutes: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    grace_minutes: int = 20
    can_create_orders: bool = False


@dataclass(frozen=True)
class ReportEvidenceRef:
    """Identify one replayable source used by an investment operating report."""

    evidence_id: str
    source_type: str
    source_id: str
    observed_at: str | None = None


@dataclass(frozen=True)
class InvestmentReportEnvelope:
    """Describe a published or degraded report independently from task state."""

    report_id: str
    report_type: InvestmentReportType
    run_id: str
    trade_date: str
    schema_version: str
    evidence_hash: str
    status: str
    content: dict[str, Any]
    evidence: tuple[ReportEvidenceRef, ...]
    can_trade: bool = False
    can_create_orders: bool = False


@dataclass(frozen=True)
class OperatingTaskResult:
    """Keep scheduler lifecycle separate from report publication lifecycle."""

    task_id: str
    job_name: str
    scheduled_for: str
    status: str
    report_id: str | None = None
    error_message: str | None = None
    can_trade: bool = False
    can_create_orders: bool = False


@dataclass(frozen=True)
class OperatingNotification:
    """Represent a local alert that can never alter investment state."""

    notification_id: str
    level: NotificationLevel
    title: str
    message: str
    source_type: str
    source_id: str
    status: str
    can_trade: bool = False
    can_create_orders: bool = False


@dataclass(frozen=True)
class CopilotEvidenceItem:
    """Hold one cited, source-owned fact exposed through Evidence Reader."""

    evidence_id: str
    source: str
    observed_at: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class CopilotEvidenceBundle:
    """Provide the only business evidence that a Copilot agent may receive."""

    run_id: str
    research_date: str
    strategy_version: str
    factor_version: str
    data_version: str
    evidence_hash: str
    evidence_items: list[CopilotEvidenceItem]
    data_gaps: list[str]
    generated_at: str
    can_trade: bool = False
    used_for_execution: bool = False


@dataclass(frozen=True)
class PromptSpec:
    """Version one immutable Copilot prompt and its bounded generation settings."""

    agent_type: CopilotAgentType
    prompt_version: str
    system_prompt: str
    temperature: float
    max_tokens: int
    created_at: str


@dataclass(frozen=True)
class ModelJsonCompletion:
    """Carry validated provider JSON without granting execution capabilities."""

    content: dict[str, Any]
    model: str
    generated_at: str
    used_for_execution: bool = False
