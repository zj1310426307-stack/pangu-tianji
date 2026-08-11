from datetime import date
from uuid import UUID
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class KillSwitchRequest(BaseModel):
    """Validate local safety-stop changes without exposing live settings."""

    enabled: bool


class PaperBuyRequest(BaseModel):
    """Accept one explicit local paper buy with a caller-stable retry key."""

    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    quantity: int = Field(ge=100, le=10_000_000)
    idempotency_key: UUID


class PaperSellRequest(BaseModel):
    """Accept one explicit reduce-only local paper sell."""

    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    quantity: int = Field(ge=1, le=10_000_000)
    idempotency_key: UUID


class PaperOrderPreviewRequest(BaseModel):
    """Request one server-side paper estimate without creating an order."""

    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    quantity: int = Field(ge=1, le=10_000_000)
    side: Literal["BUY", "SELL"]


class PaperOrderPreviewResponse(BaseModel):
    """Expose fees, projected account state and the exact blocking reason."""

    side: Literal["BUY", "SELL"]
    symbol: str
    name: str
    quantity: int
    allowed: bool
    blocked_reason: str | None
    blocked_state: str
    requested_price: float
    estimated_fill_price: float
    gross_value: float
    fee_breakdown: dict[str, float]
    estimated_slippage_cost: float
    estimated_realized_pnl: float
    before: dict[str, float | int]
    after: dict[str, float | int]
    quote_timestamp_ms: int
    live_trading_enabled: Literal[False]
    can_submit_orders: Literal[False]


class PaperBrokerOrderPreviewRequest(BaseModel):
    """Request a broker-style paper preview for an instant or DAY limit order."""

    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    quantity: int = Field(ge=1, le=10_000_000)
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None = Field(default=None, gt=0, le=100_000)


class PaperBrokerOrderPreviewResponse(PaperOrderPreviewResponse):
    """Add order-style and matchability evidence to the existing estimate."""

    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None
    time_in_force: Literal["DAY"]
    marketable_now: bool


class PaperBrokerOrderRequest(PaperBrokerOrderPreviewRequest):
    """Create one idempotent local paper order; it never reaches a broker."""

    idempotency_key: UUID


class PaperBrokerOrderResponse(BaseModel):
    """Return the local order, optional fill and refreshed paper account."""

    quote_timestamp_ms: int
    order: dict[str, Any]
    trade: dict[str, Any] | None = None
    account: dict[str, Any]
    receipt: dict[str, Any] = Field(default_factory=dict)
    live_trading_enabled: Literal[False]
    can_submit_orders: Literal[False]


class PaperBrokerCancelResponse(BaseModel):
    """Return a cancelled local order and released account resources."""

    order: dict[str, Any]
    account: dict[str, Any]
    live_trading_enabled: Literal[False]
    can_submit_orders: Literal[False]


class PaperBrokerMatchResponse(BaseModel):
    """Expose one explicit polling-snapshot match cycle."""

    matched: list[dict[str, Any]]
    waiting: list[dict[str, Any]]
    expired_count: int
    account: dict[str, Any]
    matching_model: Literal["ths_polling_snapshot_all_or_none"]
    quote_timestamp_ms: int | None = None
    live_trading_enabled: Literal[False]
    can_submit_orders: Literal[False]


class MarketQuoteResponse(BaseModel):
    """Expose one THS polling snapshot without implying a streaming feed."""

    symbol: str
    name: str
    source: Literal["ths_finance_snapshot"]
    feed_type: Literal["polling_snapshot"]
    timestamp_ms: int
    observed_at: str
    age_seconds: float
    stale: bool
    market_open: bool
    last_price: float
    price_change: float
    price_change_ratio_pct: float
    open_price: float
    high_price: float
    low_price: float
    prev_price: float
    volume: float
    turnover: float
    can_submit_paper_order: bool
    can_submit_paper_buy: bool = False
    can_submit_paper_sell: bool = False
    available_quantity: int = 0
    max_buy_quantity: int = 0
    blocked_reason: str | None
    sell_blocked_reason: str | None = None
    live_trading_enabled: Literal[False]
    can_submit_orders: Literal[False]


class PaperBuyResponse(BaseModel):
    """Return the persisted local order and refreshed paper account view."""

    quote_timestamp_ms: int
    order: dict[str, Any]
    trade: dict[str, Any] | None = None
    account: dict[str, Any]
    receipt: dict[str, Any] = Field(default_factory=dict)
    live_trading_enabled: Literal[False]
    can_submit_orders: Literal[False]


class PaperSellResponse(PaperBuyResponse):
    """Return the persisted local sell and refreshed paper account view."""


class ModelExplanationRequest(BaseModel):
    """Allow a model to reference only one completed run id."""

    run_id: UUID


class DeepSeekConfigurationRequest(BaseModel):
    """Accept a write-only key and one allowlisted DeepSeek model."""

    api_key: SecretStr = Field(min_length=20, max_length=512)
    model: Literal["deepseek-v4-flash", "deepseek-v4-pro"] = (
        "deepseek-v4-flash"
    )


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["pangu-tianji"]
    version: str


class KillSwitchResponse(BaseModel):
    enabled: bool
    live_trading_enabled: Literal[False]


class ModelStatusResponse(BaseModel):
    state: Literal["not_configured", "checking", "connected", "error"]
    provider: str
    model: str | None
    base_url: str | None
    last_checked_at: str | None
    message: str
    can_trade: Literal[False]
    api_key_configured: bool


class ModelExplanationResponse(BaseModel):
    summary: str
    risks: list[str]
    model: str
    generated_at: str
    used_for_execution: Literal[False]


class CopilotReportRequest(BaseModel):
    """Request one evidence-grounded AI report without execution parameters."""

    report_type: Literal[
        "morning_report",
        "close_review",
        "stock_analysis",
        "portfolio_analysis",
        "risk_alert",
        "coach_review",
    ]
    run_id: str | None = Field(default=None, min_length=8, max_length=180)
    symbol: str | None = Field(default=None, pattern=r"^\d{6}\.(SH|SZ)$")


class CopilotReportResponse(BaseModel):
    """Expose one audited Copilot report with immutable evidence references."""

    report_id: str
    run_id: str
    agent_type: Literal["research", "portfolio", "risk", "review", "coach"]
    report_type: Literal[
        "morning_report",
        "close_review",
        "stock_analysis",
        "portfolio_analysis",
        "risk_alert",
        "coach_review",
    ]
    subject_symbol: str | None
    model_version: str
    prompt_version: str
    evidence_ids: list[str]
    evidence_hash: str
    content: dict[str, Any]
    status: Literal["published", "rejected"]
    error_message: str | None
    created_time: str
    used_for_execution: Literal[False]
    can_trade: Literal[False]
    evaluation: dict[str, Any] | None = None


class CopilotMemoryRequest(BaseModel):
    """Save one explicit user-confirmed investment memory outside trading state."""

    category: Literal["preference", "decision", "error_pattern", "lesson"]
    content: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CopilotMemoryResponse(BaseModel):
    """Return one sourced memory that can never affect order execution."""

    memory_id: str
    category: Literal["profile", "preference", "decision", "error_pattern", "lesson"]
    content: str
    source: str
    source_report_id: str | None
    evidence_ids: list[str]
    confidence: float
    status: Literal["candidate", "confirmed"]
    created_time: str
    confirmed_time: str | None
    can_affect_execution: Literal[False]


class CopilotRatingRequest(BaseModel):
    """Attach an explicit human quality score to an existing AI report."""

    rating: int = Field(ge=1, le=5)
    note: str = Field(default="", max_length=1000)


class WorkbenchAvailability(BaseModel):
    """Describe one data source without conflating unavailable states."""

    key: str
    label: str
    state: Literal["available", "unavailable", "stale"]
    source: str
    message: str


class AssetValuation(BaseModel):
    """Canonical ValuationService output shared by every account-facing page."""

    service_version: Literal["valuation-v1.0.0"]
    valued_at: str
    cash: float
    available_cash: float
    frozen_cash: float
    market_value: float
    equity: float
    pnl: float
    realized_pnl: float
    unrealized_pnl: float
    pnl_reconciliation_gap: float
    initial_cash: float
    peak_equity: float
    drawdown: float
    max_drawdown: float
    total_return: float
    cash_ratio: float
    exposure_ratio: float
    committed_exposure_value: float
    committed_exposure_ratio: float
    position_count: int
    available_position_count: int
    total_fees: float
    turnover: float
    fee_drag_pct: float


class AssetValuationPoint(BaseModel):
    """One persisted or current server-valued point for the equity chart."""

    trade_date: str
    cash: float
    market_value: float
    equity: float
    drawdown: float
    valuation_point: bool = False


class PaperReviewAccount(BaseModel):
    """Expose the persisted local paper account without brokerage capabilities."""

    service_version: Literal["valuation-v1.0.0"]
    valued_at: str
    cash: float
    available_cash: float
    frozen_cash: float
    market_value: float
    equity: float
    peak_equity: float
    drawdown: float
    kill_switch: bool
    live_trading_enabled: Literal[False]
    initial_cash: float
    total_return: float
    cash_ratio: float
    max_drawdown: float
    position_count: int
    available_position_count: int
    realized_pnl: float
    unrealized_pnl: float
    net_pnl: float
    pnl: float
    pnl_reconciliation_gap: float
    exposure_ratio: float
    total_fees: float
    turnover: float
    fee_drag_pct: float
    limits: dict[str, float | int]


class PaperReviewPosition(BaseModel):
    """Describe one stock actually held by the persistent MockBroker account."""

    symbol: str
    name: str
    quantity: int
    available_quantity: int
    frozen_quantity: int = 0
    sellable_quantity: int = 0
    average_cost: float
    acquired_date: str
    pending_exit: int
    last_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    weight: float
    valuation_source: Literal[
        "ths_polling_snapshot", "latest_paper_nav", "average_cost_fallback"
    ]
    holding_days: int
    mfe_pct: float
    mae_pct: float
    stop_price: float
    distance_to_stop_pct: float
    risk_state: Literal[
        "NORMAL", "LOSS_ALERT", "T1_LOCKED", "ORDER_FROZEN", "PENDING_EXIT"
    ]


class PaperReviewNav(BaseModel):
    """Represent one persisted paper-account NAV point for the recap chart."""

    trade_date: str
    cash: float
    market_value: float
    equity: float
    drawdown: float
    positions_json: str
    daily_return: float


class PaperReviewPerformance(BaseModel):
    """Summarize realized, unrealized and closed-sell performance evidence."""

    net_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    pnl_reconciliation_gap: float
    sell_trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    win_rate: float | None
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    average_win: float | None
    average_loss: float | None
    best_day_return: float | None
    worst_day_return: float | None
    nav_days: int
    turnover_ratio: float


class PaperReviewAttribution(BaseModel):
    """Attribute paper PnL to one held or previously sold symbol."""

    symbol: str
    name: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    weight: float


class PaperReviewValuation(BaseModel):
    """Describe the current valuation source and freshness."""

    source: str
    observed_at: str | None
    age_seconds: float | None
    stale: bool
    message: str


class PaperReviewRiskFlag(BaseModel):
    """Expose one deterministic review alert without investment advice."""

    level: Literal["success", "warning", "danger"]
    code: str
    message: str


class DisciplineDimension(BaseModel):
    """Represent one auditable discipline dimension."""

    key: str
    label: str
    score: int | None
    availability: Literal["available", "unavailable"]
    evidence: str


class DisciplineScore(BaseModel):
    """Score evidence completeness without claiming investment quality."""

    score: int | None
    grade: str
    meaning: str
    dimensions: list[DisciplineDimension]


class WorkbenchActivitySummary(BaseModel):
    """Summarize persisted paper positions, orders, trades and cost evidence."""

    position_count: int
    order_count: int
    trade_count: int
    rejection_count: int
    unknown_order_count: int
    monitor_event_count: int
    total_fees: float
    realized_pnl: float
    unrealized_pnl: float
    turnover: float
    sell_trade_count: int
    latest_activity_date: str | None


class WorkbenchResponse(BaseModel):
    """Contract for the paper-account review workspace without a whitelist."""

    version: Literal["1.0.0"]
    generated_at: str
    mode: Literal["paper_portfolio_review"]
    source_nav_date: str | None
    account: PaperReviewAccount
    asset_valuation: AssetValuation
    positions: list[PaperReviewPosition]
    nav: list[PaperReviewNav]
    equity_curve: list[AssetValuationPoint]
    performance: PaperReviewPerformance
    fee_breakdown: dict[str, float]
    symbol_attribution: list[PaperReviewAttribution]
    valuation: PaperReviewValuation
    risk_flags: list[PaperReviewRiskFlag]
    activity: dict[str, list[dict[str, Any]]]
    discipline: DisciplineScore
    activity_summary: WorkbenchActivitySummary
    data_availability: list[WorkbenchAvailability]
    can_submit_orders: Literal[False]
    provenance: str


class InvestmentOperatingReportResponse(BaseModel):
    """Return one immutable Daily Investment OS report and its evidence index."""

    report_id: str
    report_type: Literal[
        "morning_report", "intraday_monitor", "closing_review", "weekly_report"
    ]
    trade_date: str
    run_id: str
    portfolio_id: str | None
    agent_version: str
    schema_version: str
    evidence_hash: str
    content: dict[str, Any]
    status: Literal["published", "degraded"]
    created_time: str
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    notification: dict[str, Any] | None = None


class InvestmentNotificationResponse(BaseModel):
    """Expose one local notification with execution capabilities disabled."""

    notification_id: str
    level: Literal["INFO", "WARNING", "CRITICAL"]
    title: str
    message: str
    source_type: str
    source_id: str
    idempotency_key: str
    channels: list[str]
    delivery: dict[str, Any]
    status: Literal["unread", "read"]
    created_time: str
    read_time: str | None
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class InvestmentOSDashboardResponse(BaseModel):
    """Contract for the read-only personal investment operating dashboard."""

    service_version: Literal["daily-investment-os-v1.0.0"]
    generated_at: str
    trade_date: str
    workflow_state: dict[str, Any]
    asset_valuation: dict[str, Any]
    portfolio: dict[str, Any]
    risk: dict[str, Any]
    market: dict[str, Any]
    opportunities: list[dict[str, Any]]
    latest_reports: dict[str, Any]
    recent_reports: list[dict[str, Any]]
    notifications: list[dict[str, Any]]
    recent_tasks: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    counts: dict[str, int]
    safety: dict[str, Any]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileSafetyResponse(BaseModel):
    """Declare the immutable capability ceiling shared by every mobile response."""

    live_trading_enabled: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_modify_strategy: Literal[False]
    can_modify_portfolio: Literal[False]
    can_modify_risk: Literal[False]
    can_access_broker_credentials: Literal[False] = False


class MobilePairingCodeRequest(BaseModel):
    """Let a loopback administrator lock one pairing code to a fixed role."""

    role: Literal["viewer", "admin"] = "admin"


class MobilePairingCodeResponse(BaseModel):
    """Return one short-lived code once; the server stores only its salted hash."""

    pairing_id: str = Field(pattern=r"^pair-[a-f0-9]{24}$")
    pairing_code: str = Field(pattern=r"^\d{8}$")
    expires_at: str
    expires_in_seconds: int = Field(ge=60, le=900)
    attempts_allowed: int = Field(ge=1, le=10)
    role: Literal["viewer", "admin"]
    single_use: Literal[True]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobilePairRequest(BaseModel):
    """Exchange the sole active one-time code for a role-locked mobile JWT."""

    pairing_id: str | None = Field(
        default=None,
        pattern=r"^pair-[a-f0-9]{24}$",
        max_length=40,
    )
    pairing_code: SecretStr = Field(min_length=8, max_length=8)
    device_name: str = Field(min_length=1, max_length=80)


class MobilePairResponse(BaseModel):
    """Return a bounded bearer token and its non-trading claims."""

    access_token: str = Field(min_length=80, max_length=4096)
    token_type: Literal["bearer"]
    expires_in_seconds: int = Field(ge=300, le=86400)
    expires_at: str
    subject: str
    device_id: str = Field(pattern=r"^device-[a-f0-9]{24}$")
    role: Literal["viewer", "admin"]
    scopes: list[str]
    safety: MobileSafetyResponse
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileAuthStatusResponse(BaseModel):
    """Expose mobile availability without authentication or secret material."""

    service_version: Literal["mobile-auth-v1.0.0"]
    enabled: bool
    ready: bool
    pairing_available: bool
    secret_persistence: Literal["environment", "ephemeral"]
    restart_invalidates_tokens: bool
    token_ttl_seconds: int
    pairing_ttl_seconds: int
    message: str
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileSessionResponse(BaseModel):
    """Describe the verified token claims without returning the token itself."""

    subject: str
    device_id: str
    device_name: str
    role: Literal["viewer", "admin"]
    scopes: list[str]
    issued_at: str
    expires_at: str
    token_id: str
    safety: MobileSafetyResponse
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileDashboardResponse(BaseModel):
    """Contract for the mobile investment cockpit with server-owned calculations."""

    service_version: Literal["mobile-investment-assistant-v1.0.0"]
    generated_at: str
    trade_date: str
    asset_valuation: dict[str, Any]
    daily_performance: dict[str, Any]
    allocation: dict[str, Any]
    market: dict[str, Any]
    risk: dict[str, Any]
    ai_summary: dict[str, Any]
    opportunities: list[dict[str, Any]]
    opportunities_availability: Literal["available", "unavailable"]
    data_gaps: list[str]
    notification_summary: dict[str, Any]
    workflow_state: dict[str, Any]
    store_counts: dict[str, int]
    safety: MobileSafetyResponse
    used_for_execution: Literal[False]
    can_affect_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobilePortfolioResponse(BaseModel):
    """Contract for canonical valuation, held positions and non-executable exit evidence."""

    service_version: Literal["mobile-investment-assistant-v1.0.0"]
    generated_at: str
    asset_valuation: dict[str, Any]
    positions: list[dict[str, Any]]
    target_portfolio: dict[str, Any] | None
    risk: dict[str, Any]
    exit_plan: dict[str, Any] | None
    performance: dict[str, Any]
    attribution: list[dict[str, Any]]
    data_gaps: list[str]
    safety: MobileSafetyResponse
    used_for_execution: Literal[False]
    can_affect_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileStockDetailResponse(BaseModel):
    """Contract for one evidence-linked stock page bound to a formal close run."""

    service_version: Literal["mobile-investment-assistant-v1.0.0"]
    generated_at: str
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    name: str | None
    availability: Literal["available", "unavailable"]
    formal_run_id: str | None
    research_date: str | None
    ranking: dict[str, Any] | None
    factor_analysis: dict[str, Any] | None
    risk_analysis: dict[str, Any] | None
    target_position: dict[str, Any] | None
    current_holding: dict[str, Any] | None
    exit_signal: dict[str, Any] | None
    ai_explanation: dict[str, Any] | None
    evidence: list[dict[str, Any]]
    evidence_hash: str | None
    data_gaps: list[str]
    safety: MobileSafetyResponse
    used_for_execution: Literal[False]
    can_affect_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileCopilotChatRequest(BaseModel):
    """Route a bounded question through one existing evidence-grounded AI intent."""

    intent: Literal[
        "stock_analysis",
        "portfolio_analysis",
        "risk_consultation",
        "daily_review",
        "why_selected",
    ]
    question: str = Field(min_length=1, max_length=1200)
    symbol: str | None = Field(
        default=None,
        pattern=r"^\d{6}\.(SH|SZ)$",
        max_length=9,
    )


class MobileCopilotChatResponse(BaseModel):
    """Return one audited AI answer with its immutable evidence identity."""

    service_version: Literal["mobile-investment-assistant-v1.0.0"]
    generated_at: str
    availability: Literal["available", "unavailable"]
    intent: str
    question: str
    symbol: str | None
    chat_id: str | None = None
    report_id: str | None
    run_id: str | None
    answer: dict[str, Any]
    evidence_ids: list[str]
    evidence_hash: str | None
    evaluation: dict[str, Any] | None = None
    data_gaps: list[str]
    safety: MobileSafetyResponse
    used_for_execution: Literal[False]
    can_affect_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class MobileJournalCreateRequest(BaseModel):
    """Create one idempotent investment note that never becomes a signal."""

    idempotency_key: UUID
    entry_type: Literal["buy_reason", "sell_reason", "review", "general"]
    trade_date: date
    symbol: str | None = Field(
        default=None,
        pattern=r"^\d{6}\.(SH|SZ)$",
        max_length=9,
    )
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    linked_report_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._:-]{1,180}$",
        max_length=180,
    )
    review_due_date: date | None = None


class MobileJournalUpdateRequest(BaseModel):
    """Revise a note with optimistic versioning and a retry-safe mutation key."""

    idempotency_key: UUID
    expected_version: int = Field(ge=1, le=1_000_000)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    review_due_date: date | None = None


class MobileJournalArchiveRequest(BaseModel):
    """Soft-delete one note while retaining its immutable revision audit."""

    idempotency_key: UUID
    expected_version: int = Field(ge=1, le=1_000_000)


class MobileJournalEntryResponse(BaseModel):
    """Expose one non-executable journal entry and optional revision metadata."""

    entry_id: str
    source_device_id: str
    entry_type: Literal["buy_reason", "sell_reason", "review", "general"]
    trade_date: str
    symbol: str | None
    title: str
    content: str
    linked_report_id: str | None
    review_due_date: str | None
    created_time: str
    updated_time: str | None
    archived_time: str | None
    status: Literal["active", "archived"]
    version: int
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    safety: MobileSafetyResponse | None = None
    used_for_execution: Literal[False] = False
    can_affect_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]


class StrategyPromotionDecisionRequest(BaseModel):
    """Capture an explicit, attributable human strategy-governance decision."""

    actor: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=1000)


class StrategyPromotionRequest(BaseModel):
    """Open a pending promotion request without changing strategy state."""

    requested_by: str = Field(min_length=2, max_length=100)


class StrategyLabDashboardResponse(BaseModel):
    """Expose validation evidence and approvals with zero trading capabilities."""

    service_version: Literal["strategy-validation-service-v1.0.0"]
    gate_contract: dict[str, Any]
    strategies: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    promotion_history: list[dict[str, Any]]
    counts: dict[str, int]
    safety: dict[str, Any]
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_auto_promote: Literal[False]


class StrategyEvolutionBranchRequest(BaseModel):
    """Import one immutable Validation Gate version as a research branch."""

    strategy_id: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    branch_name: str = Field(min_length=1, max_length=120)
    parent_version: str | None = Field(default=None, min_length=1, max_length=80)


class StrategyEvolutionEvaluationRequest(BaseModel):
    """Evaluate one sealed strategy review without changing strategy behavior."""

    strategy_id: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    review_id: str | None = Field(default=None, min_length=3, max_length=180)


class StrategyEvolutionComparisonRequest(BaseModel):
    """Compare two registered versions without selecting a production winner."""

    strategy_id: str = Field(min_length=2, max_length=120)
    version_a: str = Field(min_length=1, max_length=80)
    version_b: str = Field(min_length=1, max_length=80)


class StrategyEvolutionTransitionRequest(BaseModel):
    """Open a human-only sequential lifecycle request."""

    strategy_id: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    target_state: Literal[
        "DRAFT", "RESEARCH", "VALIDATED", "PAPER_RUNNING",
        "PRODUCTION_CANDIDATE", "DEPRECATED", "RETIRED",
    ]
    requested_by: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=1000)


class StrategyEvolutionDecisionRequest(BaseModel):
    """Record an attributable human lifecycle approval or rejection."""

    actor: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=1000)


class StrategyEvolutionDashboardResponse(BaseModel):
    """Expose strategy health and governance with every execution right disabled."""

    service_version: Literal["strategy-evolution-engine-v1.0.0"]
    generated_at: str
    branches: list[dict[str, Any]]
    health_history: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    lifecycle_history: list[dict[str, Any]]
    reports: list[dict[str, Any]]
    available_validation_strategies: list[dict[str, Any]]
    counts: dict[str, int]
    ai_strategy_observer: dict[str, Any]
    safety: dict[str, Any]
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_modify_strategy: Literal[False]
    can_modify_parameters: Literal[False]
    can_modify_factor_weights: Literal[False]
    can_replace_production_strategy: Literal[False]
    can_auto_transition: Literal[False]
    can_launch_experiment: Literal[False]


class AIResearchBriefRequest(BaseModel):
    """Request one evidence-grounded brief without selecting parameters or orders."""

    review_id: str | None = Field(default=None, min_length=3, max_length=180)


class AIResearchQuestionUpdateRequest(BaseModel):
    """Update only the workflow status of a non-executable research question."""

    status: Literal["OPEN", "PLANNED", "TESTED", "REJECTED", "ARCHIVED"]


class AIResearchDashboardResponse(BaseModel):
    """Expose AI Quant Research state with all mutation and trading rights disabled."""

    service_version: Literal["ai-quant-research-analyst-v1.0.0"]
    generated_at: str
    model: dict[str, Any]
    evidence: dict[str, Any]
    latest_report: dict[str, Any] | None
    reports: list[dict[str, Any]]
    memory: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    counts: dict[str, int]
    schedule: dict[str, Any]
    agents: list[dict[str, Any]]
    safety: dict[str, Any]
    used_for_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_modify_experiment: Literal[False]
    can_modify_parameters: Literal[False]
    can_modify_strategy: Literal[False]
    can_modify_factor_weights: Literal[False]
    can_modify_portfolio: Literal[False]
    can_modify_risk: Literal[False]
    can_approve_strategy: Literal[False]


class PersonalOSDashboardResponse(BaseModel):
    """Expose the backend-owned Personal Investment OS cockpit."""

    service_version: Literal["personal-investment-os-v1.0.0"]
    generated_at: str
    trade_date: str
    asset_valuation: dict[str, Any]
    market: dict[str, Any]
    portfolio: dict[str, Any]
    risk: dict[str, Any]
    investor_profile: dict[str, Any]
    personal_score: dict[str, Any]
    coach: dict[str, Any]
    investment_loop: dict[str, Any]
    strategy_validation: dict[str, Any]
    quant_research: dict[str, Any]
    events: list[dict[str, Any]]
    journals: list[dict[str, Any]]
    knowledge: list[dict[str, Any]]
    reports: list[dict[str, Any]]
    counts: dict[str, int]
    safety: dict[str, Any]
    used_for_execution: Literal[False]
    can_affect_execution: Literal[False]
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_modify_strategy: Literal[False]
    can_modify_factor_weights: Literal[False]
    can_modify_portfolio: Literal[False]
    can_modify_risk: Literal[False]
    can_approve_strategy: Literal[False]
    can_launch_experiment: Literal[False]


class DataIntelligenceDashboardResponse(BaseModel):
    """Expose persisted Data Intelligence state without evaluating data on GET."""

    service_version: Literal["data-intelligence-platform-v1.0.0"]
    generated_at: str
    data_center: dict[str, Any]
    health: dict[str, Any]
    incidents: dict[str, Any]
    catalog: list[dict[str, Any]]
    lineage: dict[str, Any] | None
    counts: dict[str, int]
    safety: dict[str, Any]
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_modify_historical_data: Literal[False]
    can_modify_strategy: Literal[False]
    can_modify_factor_weights: Literal[False]
    can_bypass_gate: Literal[False]


class DataHealthResponse(BaseModel):
    """Return one immutable Data Health Score and its five fixed components."""

    service_version: str | None = None
    health_id: str | None = None
    run_id: str
    monitor_version: str | None = None
    data_version: str
    research_date: str
    status: Literal["NORMAL", "WARNING", "ERROR", "BLOCKED"]
    score: float
    components: dict[str, Any]
    issues: list[dict[str, Any]]
    evidence_hash: str | None = None
    blocking: bool
    publish_allowed: bool
    preview: bool
    created_time: str | None = None
    evaluated_at: str | None = None
    weights: dict[str, int] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_modify_historical_data: Literal[False]
    can_bypass_gate: Literal[False]


class DataIncidentResponse(BaseModel):
    """Expose one auditable incident whose acknowledgement cannot release the gate."""

    incident_id: str
    run_id: str
    category: str
    code: str
    level: Literal["WARNING", "ERROR", "BLOCKED"]
    description: str
    evidence: dict[str, Any]
    fingerprint: str
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
    created_time: str
    acknowledged_time: str | None = None
    resolved_time: str | None = None
    can_trade: Literal[False]
    can_create_orders: Literal[False]
    can_bypass_gate: Literal[False]


class InvestorDigitalTwinUpdateRequest(BaseModel):
    """Update descriptive preferences without changing production constraints."""

    capital: float = Field(gt=0, le=1_000_000_000)
    risk_level: Literal["conservative", "balanced", "aggressive"]
    holding_period: Literal["short", "medium", "long"]
    investment_style: Literal[
        "balanced", "value", "value_growth", "quality", "growth", "momentum",
        "low_volatility",
    ]
    max_drawdown: float = Field(ge=0.03, le=0.30)
    behavior: list[str] = Field(default_factory=list, max_length=20)
    source_profile_id: str | None = Field(default=None, max_length=120)


class PersonalInvestmentEventRequest(BaseModel):
    """Create a manual observation/review/lesson, never a trade fact."""

    idempotency_key: UUID
    event_type: Literal["OBSERVE", "REVIEW", "LEARN"]
    trade_date: date
    symbol: str | None = Field(default=None, pattern=r"^\d{6}\.(SH|SZ)$")
    name: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=1, max_length=2000)
    score: float | None = Field(default=None, ge=0, le=100)
    risk_score: float | None = Field(default=None, ge=0, le=100)
    result_pnl: float | None = None
    source_id: str | None = Field(default=None, max_length=180)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class PersonalJournalCreateRequest(BaseModel):
    """Create one idempotent investment-process journal entry."""

    idempotency_key: UUID
    entry_type: Literal["buy_reason", "sell_reason", "observation", "review", "lesson"]
    event_id: str | None = Field(default=None, max_length=180)
    trade_date: date
    symbol: str | None = Field(default=None, pattern=r"^\d{6}\.(SH|SZ)$")
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    outcome: str | None = Field(default=None, max_length=2000)
    lesson: str | None = Field(default=None, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class PersonalJournalUpdateRequest(BaseModel):
    """Update a journal using optimistic version control."""

    expected_version: int = Field(ge=1, le=1_000_000)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    outcome: str | None = Field(default=None, max_length=2000)
    lesson: str | None = Field(default=None, max_length=2000)


class PersonalArchiveRequest(BaseModel):
    """Soft-archive one personal record under optimistic version control."""

    expected_version: int = Field(ge=1, le=1_000_000)


class PersonalKnowledgeCreateRequest(BaseModel):
    """Create a personal knowledge note outside ResearchPipeline evidence."""

    idempotency_key: UUID
    category: Literal["company", "market", "personal_lesson"]
    subject: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=1.0, ge=0, le=1)


class PersonalReportRequest(BaseModel):
    """Generate one bounded personal report for an explicit period."""

    period_start: date | None = None
    period_end: date | None = None
