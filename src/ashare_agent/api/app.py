from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import ipaddress
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Path as ApiPath, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
import yaml

from pangu.core.exceptions import AuditStoreError, BackupError, ConfigurationError
from pangu.engineering import EngineeringService
from pangu.observability.service import ObservabilityService
from pangu.version.system_version import API_VERSION, SYSTEM_VERSION

from ..ai_copilot.copilot_service import CopilotGroundingError, CopilotService
from ..ai_copilot.evidence_reader import EvidenceReaderError
from ..ai_copilot.memory_store import CopilotStoreError
from ..model_provider import ModelProviderError
from ..model_settings import ModelSettingsError
from ..repositories.run_repository import RunRepository
from ..scheduler.job_manager import JobManager
from ..services.dashboard_service import DashboardService
from ..services.investment_dashboard_service import InvestmentDashboardService
from ..services.personal_ai_assistant_service import PersonalAIAssistantService
from ..services.daily_investment_os_service import DailyInvestmentOSService
from ..services.daily_research_service import DailyResearchService
from ..services.investment_report_center import InvestmentReportCenterError
from ..services.model_service import ModelBusyError, ModelService
from ..services.mobile_auth_service import (
    MobileAuthError,
    MobileAuthService,
    MobilePrincipal,
)
from ..services.mobile_investment_service import (
    MobileInvestmentService,
    MobileInvestmentServiceError,
)
from ..services.mobile_journal_store import MobileJournalStoreError
from ..services.run_service import RunConflictError, RunService, SafetyStopError
from ..services.workbench_service import WorkbenchService
from ..quant_lab.exceptions import InvalidStateTransitionError, QuantLabError
from ..quant_lab.validation_gate import StrategyValidationService
from ..quant_ai.research_memory import QuantAIStoreError
from ..quant_ai.service import AIQuantResearchService, QuantAIResearchUnavailable
from ..personal_os.service import PersonalInvestmentOSService
from ..personal_os.store import PersonalOSStoreError
from ..data_monitor import DataIntelligenceService
from ..data_monitor.contracts import DataIntelligenceError, DataQualityGateError
from ..strategy_evolution import (
    EvolutionArtifactError,
    EvolutionEvidenceError,
    EvolutionStateError,
    StrategyEvolutionService,
)
from .schemas import (
    AIResearchBriefRequest,
    AIResearchDashboardResponse,
    AIResearchQuestionUpdateRequest,
    DataHealthResponse,
    DataIncidentResponse,
    DataIntelligenceDashboardResponse,
    EngineeringBackupResponse,
    EngineeringDashboardResponse,
    EngineeringHealthResponse,
    EngineeringItemsResponse,
    ObservabilityAcknowledgeRequest,
    ObservabilityDashboardResponse,
    ObservabilityIncidentCreateRequest,
    ObservabilityIncidentStatusRequest,
    ObservabilityItemsResponse,
    ObservabilityRetentionRunRequest,
    ObservabilityTraceResponse,
    PersonalArchiveRequest,
    PersonalInvestmentEventRequest,
    PersonalJournalCreateRequest,
    PersonalJournalUpdateRequest,
    InvestmentReviewConfirmRequest,
    InvestmentReminderStatusRequest,
    PersonalKnowledgeCreateRequest,
    PersonalOSDashboardResponse,
    PersonalReportRequest,
    InvestorDigitalTwinUpdateRequest,
    HealthResponse,
    CopilotMemoryRequest,
    CopilotMemoryResponse,
    CopilotRatingRequest,
    CopilotReportRequest,
    CopilotReportResponse,
    DeepSeekConfigurationRequest,
    KillSwitchRequest,
    KillSwitchResponse,
    MarketQuoteResponse,
    ModelExplanationRequest,
    ModelExplanationResponse,
    ModelStatusResponse,
    PaperBuyRequest,
    PaperBuyResponse,
    PaperBrokerCancelResponse,
    PaperBrokerMatchResponse,
    PaperBrokerOrderPreviewRequest,
    PaperBrokerOrderPreviewResponse,
    PaperBrokerOrderRequest,
    PaperBrokerOrderResponse,
    PaperOrderPreviewRequest,
    PaperOrderPreviewResponse,
    PaperSellRequest,
    PaperSellResponse,
    WorkbenchResponse,
    InvestmentNotificationResponse,
    InvestmentOperatingReportResponse,
    InvestmentOSDashboardResponse,
    InvestmentDashboardOverviewResponse,
    PersonalAssistantQueryRequest,
    PersonalAssistantQueryResponse,
    MobileAuthStatusResponse,
    MobileCopilotChatRequest,
    MobileCopilotChatResponse,
    MobileDashboardResponse,
    MobileJournalArchiveRequest,
    MobileJournalCreateRequest,
    MobileJournalEntryResponse,
    MobileJournalUpdateRequest,
    MobilePairingCodeRequest,
    MobilePairingCodeResponse,
    MobilePairRequest,
    MobilePairResponse,
    MobilePortfolioResponse,
    MobileSessionResponse,
    MobileStockDetailResponse,
    StrategyLabDashboardResponse,
    StrategyEvolutionBranchRequest,
    StrategyEvolutionComparisonRequest,
    StrategyEvolutionDashboardResponse,
    StrategyEvolutionDecisionRequest,
    StrategyEvolutionEvaluationRequest,
    StrategyEvolutionTransitionRequest,
    StrategyPromotionDecisionRequest,
    StrategyPromotionRequest,
)


_MOBILE_STATIC_PATHS = {
    "/mobile.html",
    "/mobile.js",
    "/mobile.css",
    "/generated/client.js",
}


def _is_loopback_address(value: str | None) -> bool:
    """Recognize direct loopback clients without trusting proxy headers."""
    if not value:
        return False
    normalized = value.split("%", 1)[0]
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _is_private_lan_address(value: str | None) -> bool:
    """Allow only direct loopback, private, or link-local mobile client addresses."""
    if _is_loopback_address(value):
        return True
    if not value:
        return False
    normalized = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(address.is_private or address.is_link_local)


def _trusted_mobile_host(value: str | None) -> bool:
    """Validate a Host header against LAN addresses or an explicit env allowlist."""
    normalized = str(value or "").strip().lower().rstrip(".")
    explicit = {
        item.strip().lower().rstrip(".")
        for item in os.getenv("PANGU_MOBILE_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    if normalized in {"localhost", "testserver"} or normalized in explicit:
        return True
    return _is_private_lan_address(normalized)


def _is_mobile_static_path(path: str) -> bool:
    """Restrict remote static reads to the dedicated mobile entry and assets."""
    return path in _MOBILE_STATIC_PATHS or path.startswith("/mobile/")


def create_app(
    project_root: Path | None = None,
    run_service: RunService | None = None,
    model_service: ModelService | None = None,
    daily_research_service: DailyResearchService | None = None,
    workbench_service: WorkbenchService | None = None,
    copilot_service: CopilotService | None = None,
    investment_os_service: DailyInvestmentOSService | None = None,
    investment_job_manager: JobManager | None = None,
    mobile_enabled: bool = False,
    mobile_auth_service: MobileAuthService | None = None,
    mobile_investment_service: MobileInvestmentService | None = None,
    strategy_validation_service: StrategyValidationService | None = None,
    quant_ai_service: AIQuantResearchService | None = None,
    personal_os_service: PersonalInvestmentOSService | None = None,
    data_intelligence_service: DataIntelligenceService | None = None,
    strategy_evolution_service: StrategyEvolutionService | None = None,
    engineering_service: EngineeringService | None = None,
    observability_service: ObservabilityService | None = None,
    investment_dashboard_service: InvestmentDashboardService | None = None,
    personal_ai_assistant_service: PersonalAIAssistantService | None = None,
    port: int = 8765,
) -> FastAPI:
    """Create the local-only API and static dashboard application."""
    # Preserve an ASCII junction on Windows. Resolving it can reintroduce a path
    # that legacy-codepage SQLite/Python builds cannot open.
    selected_root = project_root or Path(__file__).absolute().parents[3]
    root = Path(os.path.abspath(selected_root))
    repository = run_service.repository if run_service else RunRepository(root)
    runs = run_service or RunService(root, repository)
    models = model_service or ModelService()
    copilot = copilot_service or CopilotService(root, models)
    daily = daily_research_service or DailyResearchService(root)
    dashboard = DashboardService(root, runs, models, repository)
    review_daily: DailyResearchService | None = None
    if workbench_service is not None:
        workbench = workbench_service
    elif hasattr(daily, "paper") and hasattr(daily, "raw"):
        workbench = WorkbenchService(
            daily.paper,
            daily.raw["paper_account"],
            models,
            daily.paper_lock,
            getattr(daily, "review_valuation", None),
            getattr(daily, "valuation_service", None),
            ((lambda: daily._now().date()) if callable(getattr(daily, "_now", None)) else None),
        )
    else:
        # Lightweight API stubs may not expose the persistent paper ledger.
        # Keep the review contract independently available in those tests.
        review_daily = DailyResearchService(root)
        workbench = WorkbenchService(
            review_daily.paper,
            review_daily.raw["paper_account"],
            models,
            review_daily.paper_lock,
            review_daily.review_valuation,
            review_daily.valuation_service,
            lambda: review_daily._now().date(),
        )
    investment_os = investment_os_service or DailyInvestmentOSService(
        root,
        daily_service=daily,
        workbench_service=workbench,
        copilot_service=copilot,
    )
    calendar_reader = getattr(daily, "_trading_days", None)
    trading_day_provider = (
        (lambda selected: selected.isoformat() in calendar_reader())
        if callable(calendar_reader)
        else None
    )
    engineering = engineering_service or EngineeringService(
        root,
        model_status_provider=models.status,
    )
    observability = observability_service or engineering.observability
    investment_jobs = investment_job_manager or JobManager(
        investment_os,
        trading_day_provider=trading_day_provider,
        observability_jobs=observability.jobs,
    )
    mobile_config: dict = {}
    raw_settings: dict = {}
    settings_path = root / "config" / "settings.yaml"
    if settings_path.is_file():
        try:
            raw_settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            mobile_config = dict(raw_settings.get("mobile_assistant") or {})
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            # Mobile config is optional; an invalid section must not break desktop mode.
            mobile_config = {}
    mobile_auth = mobile_auth_service or MobileAuthService(
        enabled=mobile_enabled,
        config=mobile_config,
    )
    mobile_active = bool(mobile_enabled or getattr(mobile_auth, "enabled", False))
    mobile_investment = mobile_investment_service
    if mobile_investment is None and mobile_active:
        mobile_investment = MobileInvestmentService(
            root,
            daily_service=daily,
            workbench_service=workbench,
            copilot_service=copilot,
            investment_os_service=investment_os,
        )
    strategy_validation = strategy_validation_service or StrategyValidationService(root)
    quant_ai = quant_ai_service or AIQuantResearchService(
        root,
        model_service=models,
        strategy_validation_service=strategy_validation,
    )
    production_profile = getattr(daily, "investment_profile", None)
    production_profile_payload = (
        production_profile.to_dict() if hasattr(production_profile, "to_dict") else {}
    )
    personal_os = personal_os_service or PersonalInvestmentOSService(
        root,
        workbench_service=workbench,
        investment_os_service=investment_os,
        quant_ai_service=quant_ai,
        strategy_validation_service=strategy_validation,
        daily_research_service=daily,
        production_profile=production_profile_payload,
        review_config=(raw_settings.get("investment_review_loop") if settings_path.is_file() else None),
    )
    investment_dashboard = investment_dashboard_service or InvestmentDashboardService(
        workbench_service=workbench,
        daily_research_service=daily,
        investment_os_service=investment_os,
        personal_os_service=personal_os,
        copilot_service=copilot,
    )
    personal_ai_assistant = personal_ai_assistant_service or PersonalAIAssistantService(
        investment_dashboard_service=investment_dashboard,
        copilot_service=copilot,
        personal_os_service=personal_os,
    )
    data_intelligence = data_intelligence_service or getattr(
        getattr(daily, "research", None), "data_intelligence", None
    ) or DataIntelligenceService(root)
    strategy_evolution = strategy_evolution_service or StrategyEvolutionService(
        root,
        strategy_validation_service=strategy_validation,
    )
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Retain services for the app lifetime and release the worker on exit."""
        yield
        runs.shutdown()
        daily.close()
        close_investment_os = getattr(investment_os, "close", None)
        if callable(close_investment_os):
            close_investment_os()
        close_mobile = getattr(mobile_investment, "close", None)
        if callable(close_mobile):
            close_mobile()
        if review_daily is not None:
            review_daily.close()

    app = FastAPI(
        title="盘古·天机 API",
        version=API_VERSION,
        description="使用同花顺点时数据进行七维横截面研究、生产同构回测准备与本机MockBroker模拟成交。无实盘或券商下单接口。",
        lifespan=lifespan,
    )
    app.state.project_root = root
    app.state.repository = repository
    app.state.run_service = runs
    app.state.model_service = models
    app.state.copilot_service = copilot
    app.state.dashboard_service = dashboard
    app.state.workbench_service = workbench
    app.state.daily_research_service = daily
    app.state.investment_os_service = investment_os
    app.state.investment_job_manager = investment_jobs
    app.state.mobile_enabled = mobile_active
    app.state.mobile_auth_service = mobile_auth
    app.state.mobile_investment_service = mobile_investment
    app.state.strategy_validation_service = strategy_validation
    app.state.quant_ai_service = quant_ai
    app.state.personal_os_service = personal_os
    app.state.investment_dashboard_service = investment_dashboard
    app.state.personal_ai_assistant_service = personal_ai_assistant
    app.state.data_intelligence_service = data_intelligence
    app.state.strategy_evolution_service = strategy_evolution
    app.state.engineering_service = engineering
    app.state.observability_service = observability
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=(
            ["*"] if mobile_active else ["127.0.0.1", "localhost", "testserver"]
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Ashare-Client", "X-Request-Id", "X-Trace-Id"],
    )

    @app.middleware("http")
    async def protect_local_writes(request: Request, call_next):
        """Reject browser-forgeable state changes and untrusted origins."""
        client_host = request.client.host if request.client else None
        loopback = _is_loopback_address(client_host)
        path = request.url.path
        account_mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            path.startswith("/api/v1/paper/")
            or path in {
                "/api/v1/daily-research/execute",
                "/api/v1/daily-research/monitor",
                "/api/v1/safety/kill-switch",
            }
        )
        observed = path.startswith("/api/") and not path.startswith("/api/mobile/v1/")
        try:
            observation = observability.begin_request(
                request_id=request.headers.get("x-request-id"),
                trace_id=request.headers.get("x-trace-id"),
            ) if observed else None
        except (AuditStoreError, ValueError, OSError):
            # Product availability never depends on the best-effort telemetry store.
            observation = None

        def finish_observation(response: Response, *, blocked: bool = False, error_code: str | None = None) -> Response:
            """Finish bounded request telemetry without changing the API result."""
            if observation is None:
                return response
            route = request.scope.get("route")
            operation_id = str(getattr(route, "operation_id", None) or error_code or "api.middleware")
            try:
                observability.finish_request(
                    observation[0], observation[1], operation_id=operation_id,
                    method=request.method, status_code=response.status_code,
                    blocked=blocked, error_code=error_code,
                )
            except (AuditStoreError, ValueError, OSError):
                # Telemetry is fail-open for product reads/writes and cannot own business availability.
                return response
            response.headers["X-Pangu-Trace-Id"] = observation[0].trace_id
            return response
        if not loopback:
            mobile_api = path.startswith("/api/mobile/v1/")
            mobile_static = request.method in {"GET", "HEAD"} and _is_mobile_static_path(path)
            if (
                not mobile_active
                or not _is_private_lan_address(client_host)
                or not _trusted_mobile_host(request.url.hostname)
                or not (mobile_api or mobile_static)
            ):
                return finish_observation(JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "REMOTE_ROUTE_FORBIDDEN",
                            "message": "远程设备只能访问受保护的移动助手接口与静态资源",
                        }
                    },
                ), blocked=True, error_code="REMOTE_ROUTE_FORBIDDEN")
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not path.startswith("/api/mobile/v1/")
        ):
            if request.headers.get("x-ashare-client") != "local-dashboard":
                return finish_observation(JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "LOCAL_CLIENT_REQUIRED",
                            "message": "仅允许本机控制台发起状态变更",
                        }
                    },
                ), blocked=True, error_code="LOCAL_CLIENT_REQUIRED")
            origin = request.headers.get("origin")
            if origin and origin not in {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            }:
                return finish_observation(JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "UNTRUSTED_ORIGIN",
                            "message": "请求来源不受信任",
                        }
                    },
                ), blocked=True, error_code="UNTRUSTED_ORIGIN")
        try:
            response = await call_next(request)
        except Exception as exc:
            if observation is not None:
                try:
                    observability.finish_request(
                        observation[0], observation[1], operation_id="api.unhandled",
                        method=request.method, status_code=500, error_code=type(exc).__name__,
                    )
                except (AuditStoreError, ValueError, OSError):
                    pass  # The original API exception remains authoritative.
            raise
        if path.startswith("/api/mobile/v1/") or _is_mobile_static_path(path):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; font-src 'self'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
            )
            response.headers["Cache-Control"] = "no-store"
        if account_mutation and response.status_code < 400:
            # Dashboard is a read cache only. A successful paper mutation must
            # become visible on the very next read across every module.
            investment_dashboard.invalidate()
        return finish_observation(response)

    mobile_bearer = HTTPBearer(auto_error=False)

    def require_mobile_principal(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(mobile_bearer),
    ) -> MobilePrincipal:
        """Authenticate one bounded mobile token for read-only mobile routes."""
        authorization = request.headers.get("authorization")
        if authorization and len(authorization) > 4096:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "MOBILE_AUTH_HEADER_TOO_LARGE",
                    "message": "移动认证头过长",
                },
            )
        bearer = (
            f"{credentials.scheme} {credentials.credentials}"
            if credentials is not None
            else authorization
        )
        try:
            return mobile_auth.authenticate(bearer)
        except MobileAuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    def require_mobile_admin(
        principal: MobilePrincipal = Depends(require_mobile_principal),
    ) -> MobilePrincipal:
        """Allow only admin-paired devices to create non-trading mobile audit state."""
        if principal.role != "admin":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "MOBILE_ADMIN_REQUIRED",
                    "message": "当前设备为只读viewer，不能执行移动端写操作",
                },
            )
        return principal

    def require_mobile_service() -> MobileInvestmentService:
        """Resolve the lazily-created mobile service without desktop startup side effects."""
        if mobile_investment is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "MOBILE_ASSISTANT_DISABLED",
                    "message": "移动助手未启用，请使用server.py --mobile启动",
                },
            )
        return mobile_investment

    @app.post(
        "/api/v1/mobile/pairing-codes",
        operation_id="create_mobile_pairing_code",
        response_model=MobilePairingCodeResponse,
    )
    def create_mobile_pairing_code(
        payload: MobilePairingCodeRequest,
        request: Request,
    ) -> dict:
        """Create a role-locked pairing code from the loopback administrator only."""
        client_host = request.client.host if request.client else None
        if not _is_loopback_address(client_host):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "LOCAL_ADMIN_REQUIRED",
                    "message": "配对码只能由本机管理员生成",
                },
            )
        try:
            return mobile_auth.create_pairing_code(payload.role)
        except MobileAuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/auth/status",
        operation_id="get_mobile_auth_status",
        response_model=MobileAuthStatusResponse,
    )
    def get_mobile_auth_status() -> dict:
        """Expose only mobile readiness and key persistence semantics."""
        return mobile_auth.status()

    @app.post(
        "/api/mobile/v1/auth/pair",
        operation_id="pair_mobile_device",
        response_model=MobilePairResponse,
    )
    def pair_mobile_device(payload: MobilePairRequest, request: Request) -> dict:
        """Consume the sole active one-time challenge and issue a fixed-role JWT."""
        client_host = request.client.host if request.client else "unknown"
        code = payload.pairing_code.get_secret_value()
        if len(code) != 8 or not code.isdigit():
            raise HTTPException(
                status_code=422,
                detail={"code": "PAIRING_CODE_FORMAT", "message": "配对码必须为8位数字"},
            )
        try:
            return mobile_auth.pair_device(
                pairing_id=payload.pairing_id,
                pairing_code=code,
                device_name=payload.device_name,
                client_host=client_host,
            )
        except MobileAuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/session",
        operation_id="get_mobile_session",
        response_model=MobileSessionResponse,
    )
    def get_mobile_session(
        principal: MobilePrincipal = Depends(require_mobile_principal),
    ) -> dict:
        """Return the currently verified role and scopes without echoing its JWT."""
        return mobile_auth.session(principal)

    @app.get(
        "/api/mobile/v1/dashboard",
        operation_id="get_mobile_dashboard",
        response_model=MobileDashboardResponse,
    )
    def get_mobile_dashboard(
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Return the mobile cockpit without running research, AI, monitoring or orders."""
        try:
            return service.dashboard()
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "MOBILE_DASHBOARD_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/portfolio",
        operation_id="get_mobile_portfolio",
        response_model=MobilePortfolioResponse,
    )
    def get_mobile_portfolio(
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Return canonical account valuation, positions, risk and exit evidence."""
        try:
            return service.portfolio()
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "MOBILE_PORTFOLIO_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/stocks/{symbol}",
        operation_id="get_mobile_stock_detail",
        response_model=MobileStockDetailResponse,
    )
    def get_mobile_stock_detail(
        symbol: str = ApiPath(pattern=r"^\d{6}\.(SH|SZ)$", max_length=9),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Return one formal-run-bound stock evidence page without preview fallback."""
        return service.stock_detail(symbol)

    @app.post(
        "/api/mobile/v1/copilot/chat",
        operation_id="create_mobile_copilot_chat",
        response_model=MobileCopilotChatResponse,
    )
    def create_mobile_copilot_chat(
        payload: MobileCopilotChatRequest,
        principal: MobilePrincipal = Depends(require_mobile_admin),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Generate one evidence-grounded AI report; no output enters execution state."""
        try:
            return service.chat(
                intent=payload.intent,
                question=payload.question,
                symbol=payload.symbol,
                source_device_id=principal.device_id,
            )
        except CopilotGroundingError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "MOBILE_AI_REJECTED", "message": str(exc)},
            ) from exc
        except (MobileInvestmentServiceError, EvidenceReaderError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "MOBILE_AI_EVIDENCE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        except (ModelProviderError, ModelSettingsError, RuntimeError) as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "MOBILE_AI_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/copilot/history",
        operation_id="list_mobile_copilot_history",
    )
    def list_mobile_copilot_history(
        limit: int = Query(50, ge=1, le=200),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """List prior mobile AI audits without triggering a model call."""
        return service.chat_history(limit)

    @app.get(
        "/api/mobile/v1/reports",
        operation_id="list_mobile_reports",
    )
    def list_mobile_reports(
        report_type: Literal[
            "morning_report", "intraday_monitor", "closing_review", "weekly_report"
        ] | None = None,
        limit: int = Query(50, ge=1, le=200),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """List existing Daily Investment OS reports behind mobile JWT auth."""
        return service.reports(report_type, limit)

    @app.get(
        "/api/mobile/v1/reports/{report_id}",
        operation_id="get_mobile_report",
    )
    def get_mobile_report(
        report_id: str = ApiPath(pattern=r"^[A-Za-z0-9._:-]{1,180}$", max_length=180),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Return one immutable evidence-linked operating report."""
        try:
            return service.report(report_id)
        except InvestmentReportCenterError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "MOBILE_REPORT_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/notifications",
        operation_id="list_mobile_notifications",
    )
    def list_mobile_notifications(
        limit: int = Query(100, ge=1, le=300),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """List INFO/WARNING/CRITICAL operating alerts for the mobile inbox."""
        return service.notifications(limit)

    @app.post(
        "/api/mobile/v1/notifications/{notification_id}/read",
        operation_id="mark_mobile_notification_read",
    )
    def mark_mobile_notification_read(
        notification_id: str = ApiPath(
            pattern=r"^[A-Za-z0-9._:-]{1,180}$", max_length=180
        ),
        _principal: MobilePrincipal = Depends(require_mobile_admin),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Acknowledge one local message; viewer tokens cannot mutate even this state."""
        try:
            return service.mark_notification_read(notification_id)
        except InvestmentReportCenterError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "MOBILE_NOTIFICATION_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/journal",
        operation_id="list_mobile_journal",
    )
    def list_mobile_journal(
        entry_type: Literal["buy_reason", "sell_reason", "review", "general"] | None = None,
        symbol: str | None = Query(default=None, pattern=r"^\d{6}\.(SH|SZ)$", max_length=9),
        include_archived: bool = Query(default=False),
        limit: int = Query(100, ge=1, le=300),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """List investment notes; archived entries remain available on explicit request."""
        result = service.journal_entries(
            limit=limit,
            entry_type=entry_type,
            symbol=symbol,
            include_archived=include_archived,
        )
        return result

    @app.post(
        "/api/mobile/v1/journal",
        operation_id="create_mobile_journal_entry",
        response_model=MobileJournalEntryResponse,
    )
    def create_mobile_journal_entry(
        payload: MobileJournalCreateRequest,
        principal: MobilePrincipal = Depends(require_mobile_admin),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Save one user-confirmed note that cannot become an order or strategy input."""
        try:
            return service.create_journal_entry(
                source_device_id=principal.device_id,
                idempotency_key=str(payload.idempotency_key),
                entry_type=payload.entry_type,
                trade_date=payload.trade_date.isoformat(),
                symbol=payload.symbol,
                title=payload.title,
                content=payload.content,
                linked_report_id=payload.linked_report_id,
                review_due_date=(
                    payload.review_due_date.isoformat() if payload.review_due_date else None
                ),
            )
        except MobileJournalStoreError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "MOBILE_JOURNAL_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/mobile/v1/journal/{entry_id}",
        operation_id="get_mobile_journal_entry",
        response_model=MobileJournalEntryResponse,
    )
    def get_mobile_journal_entry(
        entry_id: str = ApiPath(pattern=r"^journal-[0-9a-f-]{36}$", max_length=44),
        _principal: MobilePrincipal = Depends(require_mobile_principal),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Return one note with its update/archive revision metadata."""
        try:
            return service.journal_entry(entry_id)
        except MobileJournalStoreError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "MOBILE_JOURNAL_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.patch(
        "/api/mobile/v1/journal/{entry_id}",
        operation_id="update_mobile_journal_entry",
        response_model=MobileJournalEntryResponse,
    )
    def update_mobile_journal_entry(
        payload: MobileJournalUpdateRequest,
        entry_id: str = ApiPath(pattern=r"^journal-[0-9a-f-]{36}$", max_length=44),
        principal: MobilePrincipal = Depends(require_mobile_admin),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Update note text with optimistic locking and immutable revision audit."""
        try:
            return service.update_journal_entry(
                entry_id=entry_id,
                source_device_id=principal.device_id,
                idempotency_key=str(payload.idempotency_key),
                expected_version=payload.expected_version,
                title=payload.title,
                content=payload.content,
                review_due_date=(
                    payload.review_due_date.isoformat() if payload.review_due_date else None
                ),
            )
        except MobileJournalStoreError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "MOBILE_JOURNAL_UPDATE_REJECTED", "message": str(exc)},
            ) from exc

    @app.delete(
        "/api/mobile/v1/journal/{entry_id}",
        operation_id="archive_mobile_journal_entry",
        response_model=MobileJournalEntryResponse,
    )
    def archive_mobile_journal_entry(
        payload: MobileJournalArchiveRequest,
        entry_id: str = ApiPath(pattern=r"^journal-[0-9a-f-]{36}$", max_length=44),
        principal: MobilePrincipal = Depends(require_mobile_admin),
        service: MobileInvestmentService = Depends(require_mobile_service),
    ) -> dict:
        """Soft-delete one note and retain a revision instead of destroying evidence."""
        try:
            return service.archive_journal_entry(
                entry_id=entry_id,
                source_device_id=principal.device_id,
                idempotency_key=str(payload.idempotency_key),
                expected_version=payload.expected_version,
            )
        except MobileJournalStoreError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "MOBILE_JOURNAL_ARCHIVE_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/health",
        operation_id="get_health",
        response_model=HealthResponse,
    )
    def get_health() -> dict:
        """Report process health without implying trading or model readiness."""
        return {"status": "ok", "service": "pangu-tianji", "version": SYSTEM_VERSION}

    @app.get(
        "/api/v1/dashboard/overview",
        operation_id="get_investment_dashboard_overview",
        response_model=InvestmentDashboardOverviewResponse,
    )
    def get_investment_dashboard_overview() -> dict:
        """Return one cached, read-only cockpit instead of browser-side aggregation."""
        return investment_dashboard.overview()

    @app.post(
        "/api/v1/assistant/query",
        operation_id="query_personal_ai_assistant",
        response_model=PersonalAssistantQueryResponse,
    )
    def query_personal_ai_assistant(payload: PersonalAssistantQueryRequest) -> dict:
        """Answer one explicit fixed-intent question from read-only evidence."""
        try:
            return personal_ai_assistant.query(
                intent=payload.intent,
                symbol=payload.symbol,
                question=payload.question,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "PERSONAL_ASSISTANT_REQUEST_INVALID", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/personal-os",
        operation_id="get_personal_investment_os",
        response_model=PersonalOSDashboardResponse,
    )
    def get_personal_investment_os() -> dict:
        """Return the personal investment loop without creating any state."""
        return personal_os.dashboard()

    @app.post(
        "/api/v1/personal-os/profile",
        operation_id="update_investor_digital_twin",
    )
    def update_investor_digital_twin(payload: InvestorDigitalTwinUpdateRequest) -> dict:
        """Update only the descriptive twin, never production strategy constraints."""
        try:
            return personal_os.update_profile(payload.model_dump(mode="json"))
        except (ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "INVESTOR_DIGITAL_TWIN_REJECTED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/personal-os/events/sync",
        operation_id="sync_personal_investment_events",
    )
    def sync_personal_investment_events() -> dict:
        """Import existing MockBroker fills; this endpoint cannot submit orders."""
        try:
            return personal_os.sync_events()
        except (ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_EVENT_SYNC_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/personal-os/events",
        operation_id="list_personal_investment_events",
    )
    def list_personal_investment_events(limit: int = Query(100, ge=1, le=500)) -> dict:
        return personal_os.events(limit)

    @app.post(
        "/api/v1/personal-os/events",
        operation_id="create_personal_investment_event",
    )
    def create_personal_investment_event(payload: PersonalInvestmentEventRequest) -> dict:
        try:
            return personal_os.create_event(payload.model_dump(mode="json"))
        except (ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_EVENT_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/personal-os/journal",
        operation_id="list_personal_investment_journal",
    )
    def list_personal_investment_journal(limit: int = Query(100, ge=1, le=500)) -> dict:
        return personal_os.journals(limit)

    @app.post(
        "/api/v1/personal-os/journal",
        operation_id="create_personal_investment_journal",
    )
    def create_personal_investment_journal(payload: PersonalJournalCreateRequest) -> dict:
        try:
            return personal_os.create_journal(payload.model_dump(mode="json"))
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_JOURNAL_REJECTED", "message": str(exc)},
            ) from exc

    @app.patch(
        "/api/v1/personal-os/journal/{journal_id}",
        operation_id="update_personal_investment_journal",
    )
    def update_personal_investment_journal(
        payload: PersonalJournalUpdateRequest,
        journal_id: str = ApiPath(min_length=3, max_length=180),
    ) -> dict:
        try:
            return personal_os.update_journal(journal_id, payload.model_dump(exclude_none=True))
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_JOURNAL_UPDATE_REJECTED", "message": str(exc)},
            ) from exc

    @app.delete(
        "/api/v1/personal-os/journal/{journal_id}",
        operation_id="archive_personal_investment_journal",
    )
    def archive_personal_investment_journal(
        payload: PersonalArchiveRequest,
        journal_id: str = ApiPath(min_length=3, max_length=180),
    ) -> dict:
        try:
            return personal_os.archive_journal(journal_id, payload.expected_version)
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_JOURNAL_ARCHIVE_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/review",
        operation_id="get_investment_review_loop",
    )
    def get_investment_review_loop(sync_reminders: bool = Query(default=False)) -> dict:
        """Return the one-page review loop; optional sync never calls a model or order path."""
        try:
            return personal_os.review_dashboard(sync=sync_reminders)
        except (ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":"INVESTMENT_REVIEW_UNAVAILABLE","message":str(exc)},
            ) from exc

    @app.post(
        "/api/v1/review/journal",
        operation_id="create_investment_review_journal",
    )
    def create_investment_review_journal(payload: PersonalJournalCreateRequest) -> dict:
        """Create the same Personal OS Journal through the simplified review contract."""
        try:
            return personal_os.create_journal(payload.model_dump(mode="json"))
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":"INVESTMENT_JOURNAL_REJECTED","message":str(exc)},
            ) from exc

    @app.get(
        "/api/v1/review/journal",
        operation_id="list_investment_review_journal",
    )
    def list_investment_review_journal(limit: int = Query(100, ge=1, le=500)) -> dict:
        """List the canonical four-type Personal OS Journal."""
        return personal_os.journals(limit)

    @app.get(
        "/api/v1/review/{journal_id}/draft",
        operation_id="get_investment_review_draft",
    )
    def get_investment_review_draft(
        journal_id: str = ApiPath(min_length=3,max_length=180),
    ) -> dict:
        """Create a transient factual draft only after explicit user action."""
        try:
            return personal_os.review_draft(journal_id)
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":"INVESTMENT_REVIEW_DRAFT_REJECTED","message":str(exc)},
            ) from exc

    @app.post(
        "/api/v1/review/{journal_id}",
        operation_id="confirm_investment_review",
    )
    def confirm_investment_review(
        payload: InvestmentReviewConfirmRequest,
        journal_id: str = ApiPath(min_length=3,max_length=180),
    ) -> dict:
        """Persist one user-confirmed review and optional user-confirmed Lesson."""
        try:
            return personal_os.confirm_review(journal_id,payload.model_dump(mode="json"))
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":"INVESTMENT_REVIEW_CONFIRM_REJECTED","message":str(exc)},
            ) from exc

    @app.get(
        "/api/v1/review/reminders",
        operation_id="list_investment_review_reminders",
    )
    def list_investment_review_reminders(limit: int = Query(100, ge=1, le=500)) -> dict:
        """List only the four bounded Personal OS in-app reminder types."""
        return personal_os.reminders(limit)

    @app.post(
        "/api/v1/review/reminders/sync",
        operation_id="sync_investment_review_reminders",
    )
    def sync_investment_review_reminders() -> dict:
        """Compare saved authority snapshots without research, AI or execution side effects."""
        try:
            return personal_os.sync_review_reminders()
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":"INVESTMENT_REMINDER_SYNC_REJECTED","message":str(exc)},
            ) from exc

    @app.post(
        "/api/v1/review/reminders/{reminder_id}/status",
        operation_id="update_investment_review_reminder_status",
    )
    def update_investment_review_reminder_status(
        payload: InvestmentReminderStatusRequest,
        reminder_id: str = ApiPath(min_length=3,max_length=180),
    ) -> dict:
        """Update only OPEN/READ/DISMISSED/DONE for one in-app reminder."""
        try:
            return personal_os.update_reminder(reminder_id,payload.status)
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code":"INVESTMENT_REMINDER_STATUS_REJECTED","message":str(exc)},
            ) from exc

    @app.get(
        "/api/v1/personal-os/knowledge",
        operation_id="list_personal_knowledge",
    )
    def list_personal_knowledge(limit: int = Query(100, ge=1, le=500)) -> dict:
        return personal_os.knowledge(limit)

    @app.post(
        "/api/v1/personal-os/knowledge",
        operation_id="create_personal_knowledge",
    )
    def create_personal_knowledge(payload: PersonalKnowledgeCreateRequest) -> dict:
        try:
            return personal_os.create_knowledge(payload.model_dump(mode="json"))
        except (ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_KNOWLEDGE_REJECTED", "message": str(exc)},
            ) from exc

    @app.delete(
        "/api/v1/personal-os/knowledge/{knowledge_id}",
        operation_id="archive_personal_knowledge",
    )
    def archive_personal_knowledge(
        payload: PersonalArchiveRequest,
        knowledge_id: str = ApiPath(min_length=3, max_length=180),
    ) -> dict:
        try:
            return personal_os.archive_knowledge(knowledge_id, payload.expected_version)
        except (KeyError, ValueError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PERSONAL_KNOWLEDGE_ARCHIVE_REJECTED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/personal-os/score/refresh",
        operation_id="refresh_personal_investment_score",
    )
    def refresh_personal_investment_score() -> dict:
        return personal_os.refresh_score()

    @app.post(
        "/api/v1/personal-os/reports/coach",
        operation_id="create_personal_coach_report",
    )
    def create_personal_coach_report(payload: PersonalReportRequest) -> dict:
        return personal_os.generate_coach(
            payload.period_start.isoformat() if payload.period_start else None,
            payload.period_end.isoformat() if payload.period_end else None,
        )

    @app.post(
        "/api/v1/personal-os/reports/weekly",
        operation_id="create_personal_committee_report",
    )
    def create_personal_committee_report(payload: PersonalReportRequest) -> dict:
        return personal_os.generate_weekly(
            payload.period_start.isoformat() if payload.period_start else None,
            payload.period_end.isoformat() if payload.period_end else None,
        )

    @app.post(
        "/api/v1/personal-os/reports/monthly",
        operation_id="create_personal_monthly_review",
    )
    def create_personal_monthly_review(payload: PersonalReportRequest) -> dict:
        return personal_os.generate_monthly(
            payload.period_start.isoformat() if payload.period_start else None,
            payload.period_end.isoformat() if payload.period_end else None,
        )

    @app.get(
        "/api/v1/personal-os/reports",
        operation_id="list_personal_os_reports",
    )
    def list_personal_os_reports(limit: int = Query(100, ge=1, le=300)) -> dict:
        return personal_os.reports(limit)

    @app.get(
        "/api/v1/personal-os/reports/{report_id}",
        operation_id="get_personal_os_report",
    )
    def get_personal_os_report(report_id: str) -> dict:
        try:
            return personal_os.report(report_id)
        except (KeyError, PersonalOSStoreError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "PERSONAL_OS_REPORT_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/data-intelligence",
        operation_id="get_data_intelligence_dashboard",
        response_model=DataIntelligenceDashboardResponse,
    )
    def get_data_intelligence_dashboard() -> dict:
        """Read the persisted Data Center health cockpit without hidden evaluation."""
        try:
            return data_intelligence.dashboard()
        except DataIntelligenceError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "DATA_INTELLIGENCE_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/data-intelligence/evaluate",
        operation_id="evaluate_data_intelligence_run",
        response_model=DataHealthResponse,
    )
    def evaluate_data_intelligence_run(run_id: str | None = Query(default=None)) -> dict:
        """Explicitly evaluate immutable evidence; this never publishes research."""
        try:
            return (
                data_intelligence.evaluate_run(run_id)
                if run_id else data_intelligence.evaluate_latest()
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "DATA_RUN_NOT_FOUND", "message": str(exc)},
            ) from exc
        except (DataIntelligenceError, DataQualityGateError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "DATA_EVALUATION_FAILED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/data-intelligence/health",
        operation_id="get_data_health",
        response_model=DataHealthResponse,
    )
    def get_data_health(run_id: str | None = Query(default=None)) -> dict:
        """Read one previously evaluated Data Health Score."""
        try:
            return data_intelligence.health(run_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "DATA_HEALTH_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/data-intelligence/incidents",
        operation_id="list_data_incidents",
    )
    def list_data_incidents(
        status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"] | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List data incidents without resolving or deleting their evidence."""
        return data_intelligence.incidents(status=status, limit=limit)

    @app.post(
        "/api/v1/data-intelligence/incidents/{incident_id}/acknowledge",
        operation_id="acknowledge_data_incident",
        response_model=DataIncidentResponse,
    )
    def acknowledge_data_incident(incident_id: str) -> dict:
        """Mark an incident seen; acknowledgement cannot release a blocked gate."""
        try:
            return data_intelligence.acknowledge_incident(incident_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "DATA_INCIDENT_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/data-intelligence/catalog",
        operation_id="list_data_catalog",
    )
    def list_data_catalog(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        """List monitored immutable data versions and quality scores."""
        return data_intelligence.catalog(limit)

    @app.get(
        "/api/v1/data-intelligence/lineage/{run_id}",
        operation_id="get_data_lineage",
    )
    def get_data_lineage(run_id: str) -> dict:
        """Trace one run from provider assets to factors, experiments and reports."""
        try:
            return data_intelligence.lineage(run_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "DATA_LINEAGE_NOT_FOUND", "message": str(exc)},
            ) from exc
        except DataIntelligenceError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "DATA_LINEAGE_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/strategy-evolution",
        operation_id="get_strategy_evolution_center",
        response_model=StrategyEvolutionDashboardResponse,
    )
    def get_strategy_evolution_center() -> dict:
        """Read the persisted evolution cockpit without running an evaluation."""
        return strategy_evolution.dashboard()

    @app.post(
        "/api/v1/strategy-evolution/branches/import",
        operation_id="import_strategy_evolution_branch",
    )
    def import_strategy_evolution_branch(
        payload: StrategyEvolutionBranchRequest,
    ) -> dict:
        """Import an immutable validation version; no executable code is copied."""
        try:
            return strategy_evolution.import_branch(**payload.model_dump())
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_VERSION_NOT_FOUND", "message": str(exc)},
            ) from exc
        except (EvolutionArtifactError, EvolutionEvidenceError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_BRANCH_REJECTED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-evolution/evaluations",
        operation_id="evaluate_strategy_evolution",
    )
    def evaluate_strategy_evolution(
        payload: StrategyEvolutionEvaluationRequest,
    ) -> dict:
        """Build one sealed health observation from existing research evidence."""
        try:
            return strategy_evolution.evaluate(**payload.model_dump())
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_EVOLUTION_EVIDENCE_NOT_FOUND", "message": str(exc)},
            ) from exc
        except (EvolutionArtifactError, EvolutionEvidenceError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_EVOLUTION_EVALUATION_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/strategy-evolution/health",
        operation_id="list_strategy_evolution_health",
    )
    def list_strategy_evolution_health(
        strategy_id: str | None = Query(default=None, min_length=2, max_length=120),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List immutable Strategy Health snapshots without hidden recomputation."""
        return strategy_evolution.health(strategy_id, limit)

    @app.get(
        "/api/v1/strategy-evolution/reports/{report_id}",
        operation_id="get_strategy_evolution_report",
    )
    def get_strategy_evolution_report(report_id: str) -> dict:
        """Read one hash-verified Strategy Health Report."""
        try:
            return strategy_evolution.report(report_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_EVOLUTION_REPORT_NOT_FOUND", "message": str(exc)},
            ) from exc
        except (EvolutionArtifactError, EvolutionEvidenceError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_EVOLUTION_REPORT_INVALID", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-evolution/comparisons",
        operation_id="compare_strategy_versions",
    )
    def compare_strategy_versions(
        payload: StrategyEvolutionComparisonRequest,
    ) -> dict:
        """Persist a descriptive version comparison with no automatic winner."""
        try:
            return strategy_evolution.compare(**payload.model_dump())
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_EVOLUTION_BRANCH_NOT_FOUND", "message": str(exc)},
            ) from exc
        except EvolutionEvidenceError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_COMPARISON_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/strategy-evolution/comparisons",
        operation_id="list_strategy_evolution_comparisons",
    )
    def list_strategy_evolution_comparisons(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List comparisons without evaluating or choosing a production version."""
        return strategy_evolution.comparisons(limit)

    @app.post(
        "/api/v1/strategy-evolution/lifecycle/requests",
        operation_id="request_strategy_evolution_transition",
    )
    def request_strategy_evolution_transition(
        payload: StrategyEvolutionTransitionRequest,
    ) -> dict:
        """Open one sequential lifecycle request for later human decision."""
        try:
            return strategy_evolution.request_transition(**payload.model_dump())
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_EVOLUTION_BRANCH_NOT_FOUND", "message": str(exc)},
            ) from exc
        except EvolutionStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_TRANSITION_BLOCKED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/strategy-evolution/lifecycle",
        operation_id="list_strategy_evolution_lifecycle",
    )
    def list_strategy_evolution_lifecycle(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List human governance history without changing any lifecycle state."""
        return strategy_evolution.lifecycle_history(limit)

    @app.post(
        "/api/v1/strategy-evolution/lifecycle/requests/{request_id}/approve",
        operation_id="approve_strategy_evolution_transition",
    )
    def approve_strategy_evolution_transition(
        request_id: str,
        payload: StrategyEvolutionDecisionRequest,
    ) -> dict:
        """Approve one eligible transition through an attributable human action."""
        try:
            return strategy_evolution.decide_transition(
                request_id, approved=True, actor=payload.actor, reason=payload.reason
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_TRANSITION_NOT_FOUND", "message": str(exc)},
            ) from exc
        except EvolutionStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_TRANSITION_DECISION_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-evolution/lifecycle/requests/{request_id}/reject",
        operation_id="reject_strategy_evolution_transition",
    )
    def reject_strategy_evolution_transition(
        request_id: str,
        payload: StrategyEvolutionDecisionRequest,
    ) -> dict:
        """Reject one transition while leaving the lifecycle state unchanged."""
        try:
            return strategy_evolution.decide_transition(
                request_id, approved=False, actor=payload.actor, reason=payload.reason
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_TRANSITION_NOT_FOUND", "message": str(exc)},
            ) from exc
        except EvolutionStateError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_TRANSITION_DECISION_BLOCKED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/strategy-lab",
        operation_id="get_strategy_lab",
        response_model=StrategyLabDashboardResponse,
    )
    def get_strategy_lab() -> dict:
        """List research validation and approvals without running a gate or experiment."""
        return strategy_validation.dashboard()

    @app.get(
        "/api/v1/strategy-lab/reviews/{review_id}",
        operation_id="get_strategy_validation_review",
    )
    def get_strategy_validation_review(review_id: str) -> dict:
        """Return one immutable committee report and all gate validation records."""
        try:
            return strategy_validation.registry.review(review_id)
        except (KeyError, QuantLabError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "STRATEGY_REVIEW_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-lab/reviews/{review_id}/promotion-requests",
        operation_id="request_strategy_promotion",
    )
    def request_strategy_promotion(
        review_id: str,
        payload: StrategyPromotionRequest,
    ) -> dict:
        """Create a pending human review request; strategy state remains unchanged."""
        try:
            return strategy_validation.approvals.request(review_id, payload.requested_by)
        except (KeyError, ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_PROMOTION_REQUEST_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-lab/reviews/{review_id}/retirement-requests",
        operation_id="request_strategy_retirement",
    )
    def request_strategy_retirement(
        review_id: str,
        payload: StrategyPromotionRequest,
    ) -> dict:
        """Create an evidence-linked manual retirement request without changing state."""
        try:
            return strategy_validation.approvals.request_retirement(
                review_id, payload.requested_by
            )
        except (KeyError, ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_RETIREMENT_REQUEST_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-lab/promotions/{promotion_id}/approve",
        operation_id="approve_strategy_promotion",
    )
    def approve_strategy_promotion(
        promotion_id: str,
        payload: StrategyPromotionDecisionRequest,
    ) -> dict:
        """Apply one sequential state change only after explicit human confirmation."""
        try:
            return strategy_validation.approvals.approve(
                promotion_id, approved_by=payload.actor, reason=payload.reason
            )
        except (KeyError, ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_PROMOTION_APPROVAL_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/strategy-lab/promotions/{promotion_id}/reject",
        operation_id="reject_strategy_promotion",
    )
    def reject_strategy_promotion(
        promotion_id: str,
        payload: StrategyPromotionDecisionRequest,
    ) -> dict:
        """Record a human rejection without changing the current strategy state."""
        try:
            return strategy_validation.approvals.reject(
                promotion_id, rejected_by=payload.actor, reason=payload.reason
            )
        except (KeyError, ValueError, InvalidStateTransitionError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "STRATEGY_PROMOTION_REJECTION_BLOCKED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/ai-research",
        operation_id="get_ai_research_center",
        response_model=AIResearchDashboardResponse,
    )
    def get_ai_research_center() -> dict:
        """Return the backend-owned AI Quant Research Center snapshot."""
        return quant_ai.dashboard()

    @app.get(
        "/api/v1/ai-research/evidence",
        operation_id="get_ai_research_evidence",
    )
    def get_ai_research_evidence(
        review_id: str | None = Query(default=None, min_length=3, max_length=180),
    ) -> dict:
        """Preview the exact evidence boundary without invoking DeepSeek."""
        try:
            return quant_ai.evidence(review_id)
        except (KeyError, ValueError, QuantLabError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "AI_RESEARCH_EVIDENCE_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/ai-research/briefs",
        operation_id="create_ai_research_brief",
    )
    def create_ai_research_brief(payload: AIResearchBriefRequest) -> dict:
        """Generate one manually requested, cited brief with no execution authority."""
        try:
            return quant_ai.generate(review_id=payload.review_id, trigger="user_action")
        except QuantAIResearchUnavailable as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "AI_RESEARCH_EVIDENCE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        except (QuantAIStoreError, ValueError, QuantLabError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "AI_RESEARCH_BRIEF_REJECTED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/ai-research/reports",
        operation_id="list_ai_research_reports",
    )
    def list_ai_research_reports(limit: int = Query(50, ge=1, le=200)) -> dict:
        """List immutable AI research reports without triggering analysis."""
        return quant_ai.reports(limit)

    @app.get(
        "/api/v1/ai-research/reports/{report_id}",
        operation_id="get_ai_research_report",
    )
    def get_ai_research_report(report_id: str) -> dict:
        """Read one hash-verified AI research report."""
        try:
            return quant_ai.report(report_id)
        except (KeyError, QuantAIStoreError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "AI_RESEARCH_REPORT_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/ai-research/memory",
        operation_id="list_ai_research_memory",
    )
    def list_ai_research_memory(limit: int = Query(100, ge=1, le=300)) -> dict:
        """List evidence-linked memory candidates that never feed execution."""
        return quant_ai.memories(limit)

    @app.post(
        "/api/v1/ai-research/memory/{memory_id}/confirm",
        operation_id="confirm_ai_research_memory",
    )
    def confirm_ai_research_memory(memory_id: str) -> dict:
        """Confirm one research observation through an explicit local action."""
        try:
            return quant_ai.store.confirm_memory(memory_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "AI_RESEARCH_MEMORY_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/ai-research/questions",
        operation_id="list_ai_research_questions",
    )
    def list_ai_research_questions(limit: int = Query(100, ge=1, le=300)) -> dict:
        """List research hypotheses without launching their proposed experiments."""
        return quant_ai.questions(limit)

    @app.post(
        "/api/v1/ai-research/questions/{question_id}/status",
        operation_id="update_ai_research_question_status",
    )
    def update_ai_research_question_status(
        question_id: str,
        payload: AIResearchQuestionUpdateRequest,
    ) -> dict:
        """Move only the question workflow state; never mutate an experiment."""
        try:
            return quant_ai.store.update_question(question_id, payload.status)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "AI_RESEARCH_QUESTION_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get("/api/v1/daily-research", operation_id="get_daily_research")
    def get_daily_research() -> dict:
        """Return the latest deterministic ranking and persistent paper account."""
        return daily.dashboard()

    @app.get(
        "/api/v1/market/quote",
        operation_id="get_live_market_quote",
        response_model=MarketQuoteResponse,
    )
    def get_live_market_quote(
        symbol: str = Query(pattern=r"^\d{6}\.(SH|SZ)$"),
    ) -> dict:
        """Return one THS polling snapshot and truthful paper-buy readiness."""
        try:
            return daily.quote(symbol)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "QUOTE_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/orders/preview",
        operation_id="preview_paper_order",
        response_model=PaperOrderPreviewResponse,
    )
    def preview_paper_order(payload: PaperOrderPreviewRequest) -> dict:
        """Requote and estimate one paper order without creating ledger activity."""
        try:
            return daily.preview_manual_order(
                payload.side,
                payload.symbol,
                payload.quantity,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PAPER_PREVIEW_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/broker/orders/preview",
        operation_id="preview_broker_paper_order",
        response_model=PaperBrokerOrderPreviewResponse,
    )
    def preview_broker_paper_order(
        payload: PaperBrokerOrderPreviewRequest,
    ) -> dict:
        """Preview a local instant or DAY limit order against a fresh snapshot."""
        try:
            return daily.preview_broker_order(
                payload.side,
                payload.symbol,
                payload.quantity,
                payload.order_type,
                payload.limit_price,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "BROKER_PAPER_PREVIEW_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/broker/orders",
        operation_id="submit_broker_paper_order",
        response_model=PaperBrokerOrderResponse,
    )
    def submit_broker_paper_order(payload: PaperBrokerOrderRequest) -> dict:
        """Create a persistent local paper order without any brokerage link."""
        try:
            return daily.submit_broker_order(
                payload.side,
                payload.symbol,
                payload.quantity,
                payload.order_type,
                payload.limit_price,
                str(payload.idempotency_key),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "BROKER_PAPER_ORDER_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/broker/orders/{client_order_id}/cancel",
        operation_id="cancel_broker_paper_order",
        response_model=PaperBrokerCancelResponse,
    )
    def cancel_broker_paper_order(client_order_id: str) -> dict:
        """Cancel an active local paper order and release reservations."""
        try:
            return daily.cancel_broker_order(client_order_id)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "BROKER_PAPER_CANCEL_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/broker/match",
        operation_id="match_broker_paper_orders",
        response_model=PaperBrokerMatchResponse,
    )
    def match_broker_paper_orders() -> dict:
        """Run one explicit fresh-snapshot matching cycle for active paper orders."""
        try:
            return daily.match_broker_orders()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "BROKER_PAPER_MATCH_PAUSED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/orders/buy",
        operation_id="submit_paper_buy",
        response_model=PaperBuyResponse,
    )
    def submit_paper_buy(payload: PaperBuyRequest) -> dict:
        """Persist a user-confirmed local BUY after server-side requote and risk checks."""
        try:
            return daily.manual_buy(
                payload.symbol,
                payload.quantity,
                str(payload.idempotency_key),
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PAPER_BUY_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/paper/orders/sell",
        operation_id="submit_paper_sell",
        response_model=PaperSellResponse,
    )
    def submit_paper_sell(payload: PaperSellRequest) -> dict:
        """Persist a user-confirmed reduce-only SELL after server-side requote."""
        try:
            return daily.manual_sell(
                payload.symbol,
                payload.quantity,
                str(payload.idempotency_key),
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "PAPER_SELL_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post("/api/v1/daily-research/collect", operation_id="collect_daily_research")
    def collect_daily_research(force: bool = Query(default=False)) -> dict:
        """Collect a new daily ranking; local-write middleware protects this action."""
        try:
            return daily.collect(force=force)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "RESEARCH_FAILED", "message": str(exc)}) from exc

    @app.post(
        "/api/v1/daily-research/preview",
        operation_id="refresh_intraday_candidates",
    )
    def refresh_intraday_candidates() -> dict:
        """Refresh a read-only intraday ranking that cannot feed paper execution."""
        try:
            return daily.refresh_preview()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "INTRADAY_PREVIEW_FAILED", "message": str(exc)},
            ) from exc

    @app.post("/api/v1/daily-research/execute", operation_id="execute_daily_paper_plan")
    def execute_daily_paper_plan() -> dict:
        """Execute the newest valid plan against Mock-only persistent account state."""
        try:
            return daily.execute()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "PAPER_EXECUTION_BLOCKED", "message": str(exc)}) from exc

    @app.post("/api/v1/daily-research/monitor", operation_id="monitor_daily_paper_positions")
    def monitor_daily_paper_positions() -> dict:
        """Poll fresh quotes and enforce paper stop-loss rules only."""
        try:
            return daily.monitor()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "MONITOR_FAILED", "message": str(exc)}) from exc

    @app.get(
        "/api/v1/workbench",
        operation_id="get_decision_workbench",
        response_model=WorkbenchResponse,
    )
    def get_decision_workbench() -> dict:
        """Return paper recap and persist only verified same-day NAV evidence."""
        try:
            return workbench.get()
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "WORKBENCH_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/investment-os",
        operation_id="get_investment_os",
        response_model=InvestmentOSDashboardResponse,
    )
    def get_investment_os() -> dict:
        """Return the read-only daily investment operating dashboard."""
        try:
            return investment_os.dashboard()
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "INVESTMENT_OS_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/investment-os/reports",
        operation_id="list_investment_reports",
    )
    def list_investment_reports(
        report_type: Literal[
            "morning_report", "intraday_monitor", "closing_review", "weekly_report"
        ] | None = None,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict:
        """List operating reports without starting a scheduler or model call."""
        return investment_os.reports(report_type, limit)

    @app.get(
        "/api/v1/investment-os/reports/{report_id}",
        operation_id="get_investment_report",
        response_model=InvestmentOperatingReportResponse,
    )
    def get_investment_report(report_id: str) -> dict:
        """Return one immutable report with its evidence references."""
        try:
            return investment_os.report(report_id)
        except InvestmentReportCenterError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTMENT_REPORT_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/investment-os/notifications",
        operation_id="list_investment_notifications",
    )
    def list_investment_notifications(
        limit: int = Query(100, ge=1, le=300),
    ) -> dict:
        """List local INFO/WARNING/CRITICAL operating notifications."""
        return investment_os.notification_items(limit)

    @app.post(
        "/api/v1/investment-os/jobs/{job_name}/run",
        operation_id="run_investment_os_job",
    )
    def run_investment_os_job(
        job_name: Literal[
            "morning_report", "intraday_monitor", "closing_review", "weekly_report"
        ],
        force: bool = Query(default=False),
    ) -> dict:
        """Run one user-requested report job; this endpoint cannot create orders."""
        try:
            return investment_jobs.run(
                job_name,
                trigger="user_action",
                force=force,
            )
        except (RuntimeError, ValueError, InvestmentReportCenterError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "INVESTMENT_JOB_BLOCKED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/investment-os/notifications/{notification_id}/read",
        operation_id="mark_investment_notification_read",
        response_model=InvestmentNotificationResponse,
    )
    def mark_investment_notification_read(notification_id: str) -> dict:
        """Acknowledge one in-app alert without changing investment state."""
        try:
            return investment_os.mark_notification_read(notification_id)
        except InvestmentReportCenterError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "INVESTMENT_NOTIFICATION_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/engineering",
        operation_id="get_engineering_dashboard",
        response_model=EngineeringDashboardResponse,
    )
    def get_engineering_dashboard() -> dict:
        """Read the latest engineering state without running checks or backups."""
        return engineering.dashboard()

    @app.get("/api/v1/engineering/versions", operation_id="get_engineering_versions")
    def get_engineering_versions() -> dict:
        """Return the canonical immutable version manifest."""
        return engineering.dashboard()["versions"]

    @app.get("/api/v1/engineering/config", operation_id="get_engineering_configuration")
    def get_engineering_configuration() -> dict:
        """Return the redacted versioned configuration snapshot."""
        return engineering.dashboard()["configuration"]

    @app.get(
        "/api/v1/engineering/events",
        operation_id="list_engineering_events",
        response_model=EngineeringItemsResponse,
    )
    def list_engineering_events(limit: int = Query(default=50, ge=1, le=500)) -> dict:
        """List bounded structured engineering events."""
        return engineering.list_events(limit)

    @app.get(
        "/api/v1/engineering/backups",
        operation_id="list_engineering_backups",
        response_model=EngineeringItemsResponse,
    )
    def list_engineering_backups(limit: int = Query(default=30, ge=1, le=365)) -> dict:
        """List immutable backup audit records."""
        return engineering.list_backups(limit)

    @app.post(
        "/api/v1/engineering/health/run",
        operation_id="run_engineering_health",
        response_model=EngineeringHealthResponse,
    )
    def run_engineering_health() -> dict:
        """Run read-only engineering checks; this endpoint cannot create orders."""
        return engineering.run_health()

    @app.post(
        "/api/v1/engineering/backups",
        operation_id="create_engineering_backup",
        response_model=EngineeringBackupResponse,
    )
    def create_engineering_backup() -> dict:
        """Create a local verified evidence backup with no investment side effects."""
        try:
            return engineering.create_backup()
        except (BackupError, ConfigurationError) as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "ENGINEERING_BACKUP_FAILED", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/observability",
        operation_id="get_observability_dashboard",
        response_model=ObservabilityDashboardResponse,
    )
    def get_observability_dashboard(hours: int = Query(default=24, ge=1, le=2160)) -> dict:
        """Read the server-composed observability dashboard."""
        return observability.dashboard(hours=hours)

    @app.get(
        "/api/v1/observability/metrics",
        operation_id="list_observability_metrics",
        response_model=ObservabilityItemsResponse,
    )
    def list_observability_metrics(
        metric_name: str | None = Query(default=None, min_length=3, max_length=96),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict:
        """List bounded metric samples over a validated time range."""
        if start and end and (end < start or (end - start).days > 90):
            raise HTTPException(status_code=422, detail={"code": "OBSERVABILITY_TIME_RANGE_INVALID", "message": "时间范围必须正序且不超过90天"})
        return observability.list_metrics(
            metric_name=metric_name,
            start=start.isoformat() if start else None,
            end=end.isoformat() if end else None,
            limit=limit,
        )

    @app.get(
        "/api/v1/observability/traces",
        operation_id="list_observability_traces",
        response_model=ObservabilityItemsResponse,
    )
    def list_observability_traces(
        query: str | None = Query(default=None, max_length=160),
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """Search trace headers by trace/job/run identifiers."""
        if start and end and (end < start or (end - start).days > 90):
            raise HTTPException(status_code=422, detail={"code": "OBSERVABILITY_TIME_RANGE_INVALID", "message": "时间范围必须正序且不超过90天"})
        return observability.list_traces(query=query, start=start.isoformat() if start else None, end=end.isoformat() if end else None, limit=limit)

    @app.get(
        "/api/v1/observability/traces/{trace_id}",
        operation_id="get_observability_trace",
        response_model=ObservabilityTraceResponse,
    )
    def get_observability_trace(trace_id: str = ApiPath(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")) -> dict:
        """Read one complete trace tree by its correlation identifier."""
        result = observability.traces.trace(trace_id)
        if not result:
            raise HTTPException(status_code=404, detail={"code": "OBSERVABILITY_TRACE_NOT_FOUND", "message": "Trace 不存在"})
        return result

    @app.get(
        "/api/v1/observability/jobs",
        operation_id="list_observability_jobs",
        response_model=ObservabilityItemsResponse,
    )
    def list_observability_jobs(
        status: str | None = Query(default=None, pattern=r"^(CREATED|RUNNING|SUCCEEDED|FAILED|BLOCKED|CANCELLED|STALE)$"),
        query: str | None = Query(default=None, max_length=160),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List job observations; this endpoint cannot run or retry jobs."""
        return observability.list_jobs(status=status, query=query, limit=limit)

    @app.get(
        "/api/v1/observability/alerts",
        operation_id="list_observability_alerts",
        response_model=ObservabilityItemsResponse,
    )
    def list_observability_alerts(
        status: str | None = Query(default=None, pattern=r"^(OPEN|ACKNOWLEDGED|RESOLVED|SUPPRESSED)$"),
        severity: str | None = Query(default=None, pattern=r"^(INFO|WARNING|ERROR|CRITICAL)$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List alert evidence and lifecycle events."""
        return observability.list_alerts(status=status, severity=severity, limit=limit)

    @app.post(
        "/api/v1/observability/alerts/{alert_id}/acknowledge",
        operation_id="acknowledge_observability_alert",
    )
    def acknowledge_observability_alert(payload: ObservabilityAcknowledgeRequest, alert_id: str = ApiPath(pattern=r"^alert-[a-f0-9]{32}$")) -> dict:
        """Acknowledge one alert manually without remediating its root cause."""
        try:
            return observability.alerts.acknowledge(alert_id, actor=payload.actor, note=payload.note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "OBSERVABILITY_ALERT_CONFLICT", "message": str(exc)}) from exc

    @app.get(
        "/api/v1/observability/incidents",
        operation_id="list_observability_incidents",
        response_model=ObservabilityItemsResponse,
    )
    def list_observability_incidents(
        status: str | None = Query(default=None, pattern=r"^(OPEN|INVESTIGATING|MITIGATED|RESOLVED|CLOSED)$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """List human-controlled engineering incidents."""
        return observability.list_incidents(status=status, limit=limit)

    @app.post("/api/v1/observability/incidents", operation_id="create_observability_incident")
    def create_observability_incident(payload: ObservabilityIncidentCreateRequest) -> dict:
        """Create one incident as an explicit local human action."""
        try:
            return observability.incidents.create(**payload.model_dump())
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail={"code": "OBSERVABILITY_INCIDENT_INVALID", "message": str(exc)}) from exc

    @app.post(
        "/api/v1/observability/incidents/{incident_id}/status",
        operation_id="update_observability_incident_status",
    )
    def update_observability_incident_status(payload: ObservabilityIncidentStatusRequest, incident_id: str = ApiPath(pattern=r"^incident-[a-f0-9]{32}$")) -> dict:
        """Apply one optimistic, audited incident state transition."""
        try:
            return observability.incidents.transition(incident_id, target=payload.status, expected_version=payload.expected_version, actor=payload.actor, note=payload.note, root_cause=payload.root_cause, resolution_note=payload.resolution_note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "OBSERVABILITY_INCIDENT_CONFLICT", "message": str(exc)}) from exc

    @app.get(
        "/api/v1/observability/slos",
        operation_id="list_observability_slos",
        response_model=ObservabilityItemsResponse,
    )
    def list_observability_slos() -> dict:
        """List the latest persisted SLO evaluations."""
        return observability.list_slos()

    @app.post("/api/v1/observability/evaluate", operation_id="evaluate_observability")
    def evaluate_observability() -> dict:
        """Explicitly sample existing evidence and evaluate SLO/alerts; no repair."""
        return observability.evaluate()

    @app.post("/api/v1/observability/retention/plan", operation_id="plan_observability_retention")
    def plan_observability_retention() -> dict:
        """Persist a retention plan without deleting any data."""
        return observability.retention.plan()

    @app.post("/api/v1/observability/retention/run", operation_id="run_observability_retention")
    def run_observability_retention(payload: ObservabilityRetentionRunRequest) -> dict:
        """Execute one exact retention plan; never clean business stores."""
        try:
            return observability.retention.run(payload.retention_id, payload.plan_hash)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "OBSERVABILITY_RETENTION_CONFLICT", "message": str(exc)}) from exc

    @app.get("/api/v1/status", operation_id="get_status")
    def get_status() -> dict:
        """Return independent runtime, safety, model, and latest-run states."""
        try:
            return dashboard.status()
        except RuntimeError as exc:
            return {
                "runtime": runs.status(),
                "safety": {
                    "safe": False,
                    "mode": "unsafe",
                    "broker": "mock",
                    "live_trading_enabled": False,
                    "manual_confirmation": True,
                    "kill_switch": True,
                    "model_in_execution": False,
                    "data_source": "ths_finance",
                    "message": str(exc),
                },
                "data": {
                    "provider": "ths_finance",
                    "kind": "historical_daily",
                    "state": "unavailable",
                    "network_required": True,
                    "real_time": False,
                    "cache_used": None,
                    "fetched_at": None,
                    "completed_through": None,
                    "message": str(exc),
                },
                "model": models.status(),
                "latest_backtest": repository.latest_completed(),
            }

    @app.post("/api/v1/runs", operation_id="start_run", status_code=202)
    def start_run() -> dict:
        """Start one parameterless paper run after revalidating the safety config."""
        try:
            dashboard.strategy_config()
            return runs.start_run()
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": "RUN_CONFLICT", "message": str(exc)}) from exc
        except SafetyStopError as exc:
            raise HTTPException(status_code=423, detail={"code": "SAFETY_STOP", "message": str(exc)}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "UNSAFE_CONFIG", "message": str(exc)}) from exc

    @app.get("/api/v1/runs/{run_id}", operation_id="get_run")
    def get_run(run_id: str) -> dict:
        """Return metadata and metrics for one immutable run record."""
        try:
            return repository.get_run(run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": str(exc)}) from exc

    @app.get("/api/v1/runs/{run_id}/equity", operation_id="get_run_equity")
    def get_run_equity(run_id: str, limit: int = Query(2000, ge=1, le=5000)) -> dict:
        """Return a bounded equity curve from a completed snapshot."""
        try:
            return {"items": repository.read_equity(run_id, limit)}
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail={"code": "EQUITY_NOT_FOUND", "message": str(exc)}) from exc

    @app.get("/api/v1/activity", operation_id="get_activity")
    def get_activity(
        run_id: str,
        kind: Literal["orders", "rejections", "trades", "signals"] = "orders",
        limit: int = Query(50, ge=1, le=500),
    ) -> dict:
        """Return bounded activity from one immutable SQLite snapshot."""
        try:
            return {"kind": kind, "items": repository.read_activity(run_id, kind, limit)}
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail={"code": "ACTIVITY_NOT_FOUND", "message": str(exc)}) from exc

    @app.get("/api/v1/strategy", operation_id="get_strategy")
    def get_strategy() -> dict:
        """Expose the safe read-only strategy and data configuration."""
        try:
            return dashboard.strategy_config()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "UNSAFE_CONFIG", "message": str(exc)}) from exc

    @app.post(
        "/api/v1/safety/kill-switch",
        operation_id="set_kill_switch",
        response_model=KillSwitchResponse,
    )
    def set_kill_switch(payload: KillSwitchRequest) -> dict:
        """Toggle a local new-run stop without changing live-trading settings."""
        result = runs.set_kill_switch(payload.enabled)
        daily.set_kill_switch(payload.enabled)
        return result

    @app.get("/api/v1/copilot/status", operation_id="get_copilot_status")
    def get_copilot_status() -> dict:
        """Return independent Copilot evidence, model and audit readiness."""
        return copilot.status()

    @app.get("/api/v1/copilot/evidence", operation_id="get_copilot_evidence")
    def get_copilot_evidence(
        report_type: str,
        run_id: str | None = None,
        symbol: str | None = Query(default=None, pattern=r"^\d{6}\.(SH|SZ)$"),
    ) -> dict:
        """Preview the exact evidence bundle without incurring a model call."""
        try:
            return copilot.evidence(
                run_id=run_id,
                report_type=report_type,
                symbol=symbol,
            )
        except (EvidenceReaderError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "COPILOT_EVIDENCE_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/copilot/reports",
        operation_id="create_copilot_report",
        response_model=CopilotReportResponse,
    )
    def create_copilot_report(payload: CopilotReportRequest) -> dict:
        """Generate and audit one cited AI report; rejected output is never published."""
        try:
            return copilot.generate(
                report_type=payload.report_type,
                run_id=payload.run_id,
                symbol=payload.symbol,
            )
        except EvidenceReaderError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "COPILOT_EVIDENCE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        except CopilotGroundingError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "COPILOT_GROUNDING_REJECTED",
                    "message": str(exc),
                    "report_id": exc.report_id,
                },
            ) from exc
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        except ModelProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "MODEL_ERROR", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            code = "MODEL_DISABLED" if str(exc) == "MODEL_DISABLED" else "COPILOT_ERROR"
            raise HTTPException(
                status_code=503 if code == "MODEL_DISABLED" else 500,
                detail={"code": code, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "COPILOT_REQUEST_INVALID", "message": str(exc)},
            ) from exc

    @app.get("/api/v1/copilot/reports", operation_id="list_copilot_reports")
    def list_copilot_reports(limit: int = Query(30, ge=1, le=100)) -> dict:
        """List persisted reports without triggering model generation."""
        return copilot.reports(limit)

    @app.get(
        "/api/v1/copilot/reports/{report_id}",
        operation_id="get_copilot_report",
        response_model=CopilotReportResponse,
    )
    def get_copilot_report(report_id: str) -> dict:
        """Return one report, citations and grounding evaluation."""
        try:
            return copilot.report(report_id)
        except CopilotStoreError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "COPILOT_REPORT_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/copilot/reports/{report_id}/rating",
        operation_id="rate_copilot_report",
    )
    def rate_copilot_report(report_id: str, payload: CopilotRatingRequest) -> dict:
        """Record explicit human feedback without changing report or trading state."""
        try:
            return copilot.rate_report(report_id, payload.rating, payload.note)
        except CopilotStoreError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "COPILOT_EVALUATION_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get("/api/v1/copilot/memory", operation_id="list_copilot_memory")
    def list_copilot_memory(limit: int = Query(100, ge=1, le=300)) -> dict:
        """Return sourced investment memory that cannot affect execution."""
        return copilot.memories(limit)

    @app.post(
        "/api/v1/copilot/memory",
        operation_id="create_copilot_memory",
        response_model=CopilotMemoryResponse,
    )
    def create_copilot_memory(payload: CopilotMemoryRequest) -> dict:
        """Save one user-confirmed preference, decision, error pattern or lesson."""
        try:
            return copilot.add_user_memory(
                category=payload.category,
                content=payload.content,
                confidence=payload.confidence,
            )
        except CopilotStoreError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "COPILOT_MEMORY_INVALID", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/copilot/memory/{memory_id}/confirm",
        operation_id="confirm_copilot_memory",
        response_model=CopilotMemoryResponse,
    )
    def confirm_copilot_memory(memory_id: str) -> dict:
        """Confirm one AI-derived memory candidate through a local user action."""
        try:
            return copilot.confirm_memory(memory_id)
        except CopilotStoreError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "COPILOT_MEMORY_NOT_FOUND", "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/model/status",
        operation_id="get_model_status",
        response_model=ModelStatusResponse,
    )
    def get_model_status() -> dict:
        """Return model configuration without secrets or execution permissions."""
        return models.status()

    @app.post(
        "/api/v1/model/deepseek/config",
        operation_id="configure_deepseek",
        response_model=ModelStatusResponse,
    )
    def configure_deepseek(payload: DeepSeekConfigurationRequest) -> dict:
        """Validate and persist a write-only DeepSeek key, then hot-reload it."""
        try:
            return models.configure_deepseek(
                payload.api_key.get_secret_value(), payload.model
            )
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "MODEL_CONFIG_INVALID", "message": str(exc)},
            ) from exc
        except ModelProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "MODEL_CONNECTION_FAILED", "message": str(exc)},
            ) from exc
        except ModelSettingsError as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "MODEL_CONFIG_SAVE_FAILED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/model/deepseek/clear",
        operation_id="clear_deepseek",
        response_model=ModelStatusResponse,
    )
    def clear_deepseek() -> dict:
        """Remove only Pangu Tianji model settings from this Windows user."""
        try:
            return models.clear_deepseek()
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        except ModelSettingsError as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "MODEL_CONFIG_CLEAR_FAILED", "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/model/connection-test",
        operation_id="test_model_connection",
        response_model=ModelStatusResponse,
    )
    def test_model_connection() -> dict:
        """Test an optional model endpoint without affecting deterministic runs."""
        try:
            result = models.test_connection()
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        if result["state"] == "not_configured":
            raise HTTPException(
                status_code=503,
                detail={"code": "MODEL_DISABLED", "message": "模型服务尚未配置"},
            )
        return result

    @app.post(
        "/api/v1/model/explanations",
        operation_id="create_model_explanation",
        response_model=ModelExplanationResponse,
    )
    def create_model_explanation(payload: ModelExplanationRequest) -> dict:
        """Generate research text from a completed snapshot only."""
        try:
            snapshot = dashboard.completed_snapshot(str(payload.run_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "RUN_SNAPSHOT_INVALID", "message": str(exc)}) from exc
        try:
            return models.explain(snapshot)
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        except ModelProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "MODEL_ERROR", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            code = "MODEL_DISABLED" if str(exc) == "MODEL_DISABLED" else "MODEL_ERROR"
            status = 503 if code == "MODEL_DISABLED" else 502
            raise HTTPException(status_code=status, detail={"code": code, "message": str(exc)}) from exc

    @app.post(
        "/api/v1/model/daily-research-explanation",
        operation_id="create_daily_research_explanation",
        response_model=ModelExplanationResponse,
    )
    def create_daily_research_explanation() -> dict:
        """Explain the latest deterministic ranking without exposing execution tools."""
        current = daily.dashboard()
        research = current.get("latest_research")
        if not research:
            raise HTTPException(
                status_code=409,
                detail={"code": "RESEARCH_NOT_READY", "message": "尚未生成Top 10候选榜"},
            )
        account = current.get("account", {})
        snapshot = {
            "latest_research": research,
            "account": {
                "cash": account.get("cash"),
                "market_value": account.get("market_value"),
                "equity": account.get("equity"),
                "drawdown": account.get("drawdown"),
                "positions": account.get("positions", []),
                "kill_switch": account.get("kill_switch"),
            },
            "safety": {
                "paper_only": True,
                "can_submit_orders": False,
                "model_can_trade": False,
            },
        }
        try:
            return models.explain_research(snapshot)
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        except ModelProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "MODEL_ERROR", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            code = "MODEL_DISABLED" if str(exc) == "MODEL_DISABLED" else "MODEL_ERROR"
            status = 503 if code == "MODEL_DISABLED" else 502
            raise HTTPException(
                status_code=status,
                detail={"code": code, "message": str(exc)},
            ) from exc

    @app.post(
        "/api/v1/model/paper-review-explanation",
        operation_id="create_paper_review_explanation",
        response_model=ModelExplanationResponse,
    )
    def create_paper_review_explanation() -> dict:
        """Explain actual paper-account evidence without exposing execution tools."""
        current = workbench.get()
        snapshot = {
            "mode": current["mode"],
            "source_nav_date": current["source_nav_date"],
            "account": current["account"],
            "positions": current["positions"],
            "activity_summary": current["activity_summary"],
            "discipline": current["discipline"],
            "data_availability": current["data_availability"],
            "safety": {
                "paper_only": True,
                "can_submit_orders": False,
                "model_can_trade": False,
            },
        }
        try:
            return models.explain_research(snapshot)
        except ModelBusyError as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": "MODEL_BUSY", "message": str(exc)},
            ) from exc
        except ModelProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "MODEL_ERROR", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            code = "MODEL_DISABLED" if str(exc) == "MODEL_DISABLED" else "MODEL_ERROR"
            status = 503 if code == "MODEL_DISABLED" else 502
            raise HTTPException(
                status_code=status,
                detail={"code": code, "message": str(exc)},
            ) from exc

    @app.get(
        "/api/v1/runs/{run_id}/artifacts/{artifact}",
        operation_id="download_run_artifact",
    )
    def download_run_artifact(run_id: str, artifact: str) -> Response:
        """Download one allowlisted completed-run artifact."""
        try:
            path = repository.artifact_path(run_id, artifact)
            return FileResponse(path, filename=path.name)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": str(exc)}) from exc

    web_dir = root / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
