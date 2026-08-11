from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from ashare_agent.api.app import create_app
from ashare_agent.services.model_service import ModelService
from ashare_agent.services.mobile_auth_service import MobileAuthError, MobileAuthService
from ashare_agent.services.mobile_journal_store import (
    MobileJournalStore,
    MobileJournalStoreError,
)
from ashare_agent.services.mobile_investment_service import MobileInvestmentService


class MutableClock:
    """Provide a deterministic timezone-aware clock for token boundary tests."""

    def __init__(self) -> None:
        self.value = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        """Return the current controlled time."""
        return self.value

    def advance(self, *, seconds: int) -> None:
        """Advance the controlled clock without sleeping."""
        self.value += timedelta(seconds=seconds)


def make_auth(clock: MutableClock, *, attempts: int = 2) -> MobileAuthService:
    """Build an enabled auth service with a deterministic test-only secret."""
    return MobileAuthService(
        enabled=True,
        config={
            "token_ttl_minutes": 5,
            "pairing_ttl_seconds": 60,
            "pairing_max_attempts": attempts,
        },
        secret=b"mobile-test-secret-that-is-at-least-32-bytes",
        now_provider=clock,
    )


def pair(
    auth: MobileAuthService,
    *,
    role: str,
    client_host: str = "192.0.2.10",
) -> tuple[dict, dict]:
    """Create and consume one administrator-selected pairing challenge."""
    challenge = auth.create_pairing_code(role=role)
    session = auth.pair_device(
        pairing_id=challenge["pairing_id"],
        pairing_code=challenge["pairing_code"],
        device_name=f"{role}-phone",
        client_host=client_host,
    )
    return challenge, session


def test_mobile_jwt_roles_signature_expiry_and_single_use() -> None:
    """Lock pairing-selected roles and reject replayed, tampered, or expired JWTs."""
    clock = MutableClock()
    auth = make_auth(clock)

    viewer_challenge, viewer_session = pair(auth, role="viewer")
    viewer = auth.authenticate(f"Bearer {viewer_session['access_token']}")
    assert viewer.role == "viewer"
    assert viewer.scopes == ("mobile:read",)
    assert viewer_session["can_trade"] is False
    assert viewer_session["can_create_orders"] is False

    with pytest.raises(MobileAuthError):
        auth.pair_device(
            pairing_id=viewer_challenge["pairing_id"],
            pairing_code=viewer_challenge["pairing_code"],
            device_name="replay-phone",
            client_host="192.0.2.11",
        )

    _admin_challenge, admin_session = pair(auth, role="admin")
    admin = auth.authenticate(f"Bearer {admin_session['access_token']}")
    assert admin.role == "admin"
    assert set(admin.scopes) == {
        "mobile:read",
        "mobile:copilot",
        "mobile:journal",
        "mobile:notifications",
    }

    parts = admin_session["access_token"].split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    with pytest.raises(MobileAuthError) as tampered:
        auth.authenticate(f"Bearer {'.'.join(parts)}")
    assert tampered.value.code == "MOBILE_TOKEN_INVALID"

    clock.advance(seconds=301)
    with pytest.raises(MobileAuthError) as expired:
        auth.authenticate(f"Bearer {admin_session['access_token']}")
    assert expired.value.code == "MOBILE_TOKEN_EXPIRED"


def test_pairing_code_ttl_attempt_limit_and_secret_non_disclosure() -> None:
    """Bound enrollment by TTL and attempts without exposing the signing secret."""
    clock = MutableClock()
    auth = make_auth(clock, attempts=2)
    secret_text = "mobile-test-secret-that-is-at-least-32-bytes"

    expired_challenge = auth.create_pairing_code(role="viewer")
    clock.advance(seconds=61)
    with pytest.raises(MobileAuthError) as expired:
        auth.pair_device(
            pairing_id=expired_challenge["pairing_id"],
            pairing_code=expired_challenge["pairing_code"],
            device_name="expired-phone",
            client_host="192.0.2.12",
        )
    assert expired.value.code == "PAIRING_CODE_EXPIRED"

    challenge = auth.create_pairing_code(role="admin")
    for expected_code in ("PAIRING_CODE_INVALID", "PAIRING_ATTEMPTS_EXHAUSTED"):
        with pytest.raises(MobileAuthError) as failure:
            auth.pair_device(
                pairing_id=challenge["pairing_id"],
                pairing_code="00000000"
                if challenge["pairing_code"] != "00000000"
                else "99999999",
                device_name="limited-phone",
                client_host="192.0.2.13",
            )
        assert failure.value.code == expected_code
    with pytest.raises(MobileAuthError):
        auth.pair_device(
            pairing_id=challenge["pairing_id"],
            pairing_code=challenge["pairing_code"],
            device_name="limited-phone",
            client_host="192.0.2.13",
        )

    serialized = repr(auth.status()) + repr(expired_challenge) + repr(challenge)
    assert secret_text not in serialized
    assert "access_token" not in auth.status()


def test_mobile_journal_is_idempotent_redacted_and_database_non_executable(
    tmp_path: Path,
) -> None:
    """Persist non-trading notes safely and reject capability escalation in SQLite."""
    store = MobileJournalStore(tmp_path / "mobile-assistant.db")
    try:
        first = store.add_entry(
            idempotency_key="journal-request-1",
            source_device_id="device-test",
            entry_type="buy_reason",
            trade_date="2026-08-09",
            symbol="600519.SH",
            title="Research note",
            content="api_key=top-secret-value should be hidden",
            linked_report_id="report-test",
            review_due_date="2026-09-08",
        )
        repeated = store.add_entry(
            idempotency_key="journal-request-1",
            source_device_id="device-test",
            entry_type="buy_reason",
            trade_date="2026-08-09",
            symbol="600519.SH",
            title="Research note",
            content="api_key=top-secret-value should be hidden",
            linked_report_id="report-test",
            review_due_date="2026-09-08",
        )
        assert first["entry_id"] == repeated["entry_id"]
        assert "top-secret-value" not in first["content"]
        assert first["can_affect_execution"] is False
        assert first["can_trade"] is False
        assert first["can_create_orders"] is False

        updated = store.update_entry(
            entry_id=first["entry_id"],
            idempotency_key="journal-update-1",
            source_device_id="device-test",
            expected_version=1,
            title="Updated research note",
            content="Review the saved evidence again",
        )
        assert updated["version"] == 2
        assert updated["status"] == "active"
        assert updated["revisions"][0]["operation"] == "update"
        with pytest.raises(MobileJournalStoreError):
            store.update_entry(
                entry_id=first["entry_id"],
                idempotency_key="journal-update-stale",
                source_device_id="device-test",
                expected_version=1,
                content="stale overwrite",
            )

        archived = store.archive_entry(
            entry_id=first["entry_id"],
            idempotency_key="journal-archive-1",
            source_device_id="device-test",
            expected_version=2,
        )
        assert archived["status"] == "archived"
        assert archived["version"] == 3
        assert [item["operation"] for item in archived["revisions"]] == [
            "update",
            "archive",
        ]
        assert store.list_entries() == []
        assert store.list_entries(include_archived=True)[0]["entry_id"] == first["entry_id"]

        with pytest.raises(MobileJournalStoreError):
            store.add_entry(
                idempotency_key="journal-request-1",
                source_device_id="device-test",
                entry_type="buy_reason",
                trade_date="2026-08-09",
                symbol="600519.SH",
                title="Conflicting retry",
                content="Different content",
            )

        independent = sqlite3.connect(store.database_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                independent.execute(
                    "UPDATE investment_journal SET can_create_orders=1 WHERE entry_id=?",
                    (first["entry_id"],),
                )
        finally:
            independent.close()
    finally:
        store.close()


class DailyEvidenceFake:
    """Expose formal/preview research while making every trading method fail loudly."""

    def __init__(self, *, formal: bool) -> None:
        self.formal = formal
        self.forbidden_calls: list[str] = []

    def dashboard(self) -> dict:
        """Return a formal close snapshot or a preview-only trap."""
        formal = {
            "mode": "formal_close_plan",
            "run_id": "2026-08-08_cross-sectional-v2.0.0_data-test_00000000",
            "research_date": "2026-08-08",
            "candidates": [
                {
                    "rank": 1,
                    "symbol": "600519.SH",
                    "name": "Kweichow Moutai",
                    "score": 88.0,
                    "industry": "consumer",
                }
            ],
        }
        return {
            "latest_research": formal if self.formal else None,
            "latest_preview": {
                "mode": "intraday_preview",
                "run_id": "preview-must-not-leak",
                "candidates": [
                    {"rank": 1, "symbol": "000001.SZ", "score": 99.0}
                ],
            },
            "plan_state": "ready" if self.formal else "missing",
            "preview_state": "available",
            "quote_feed": "polling_snapshot",
            "portfolio_risk_center": {
                "target_portfolio": {
                    "portfolio_id": "portfolio-test",
                    "target_exposure": 0.6,
                },
                "risk_assessment": {
                    "weighted_security_risk": 34.0,
                    "risk_flags": [],
                    "security_risks": [
                        {"symbol": "600519.SH", "risk_score": 32.0}
                    ],
                },
                "exit_plan": {"signals": []},
            },
        }

    def close(self) -> None:
        """Match the application lifespan without owning resources."""
        return None

    def execute(self) -> None:
        """Fail if a mobile read crosses into paper-plan execution."""
        self.forbidden_calls.append("execute")
        raise AssertionError("mobile must not execute")

    def monitor(self) -> None:
        """Fail if a mobile read crosses into stop-loss monitoring."""
        self.forbidden_calls.append("monitor")
        raise AssertionError("mobile must not monitor")

    def submit_broker_order(self, *_args, **_kwargs) -> None:
        """Fail if mobile code ever acquires an order-creation dependency."""
        self.forbidden_calls.append("submit_broker_order")
        raise AssertionError("mobile must not create orders")


class WorkbenchValuationFake:
    """Return deliberately non-additive values to detect hidden recomputation."""

    valuation = {
        "service_version": "valuation-v1.0.0",
        "valued_at": "2026-08-09T08:00:00+00:00",
        "cash": 11.0,
        "market_value": 22.0,
        "equity": 999.0,
        "pnl": -7.0,
        "drawdown": -0.123,
        "max_drawdown": -0.2,
        "exposure_ratio": 0.456,
        "cash_ratio": 0.011,
        "position_count": 1,
    }

    def get(self) -> dict:
        """Return the canonical valuation and only an old NAV point."""
        return {
            "asset_valuation": dict(self.valuation),
            "positions": [
                {
                    "symbol": "600519.SH",
                    "name": "Kweichow Moutai",
                    "quantity": 100,
                    "market_value": 22.0,
                }
            ],
            "nav": [
                {
                    "trade_date": "2000-01-01",
                    "daily_return": 0.99,
                    "total_return": 9.99,
                }
            ],
            "performance": {"total_return": 9.99},
            "risk_flags": [],
        }


class CopilotEvidenceFake:
    """Provide formal evidence and count generation separately from reads."""

    run_id = "2026-08-08_cross-sectional-v2.0.0_data-test_00000000"

    def __init__(self) -> None:
        self.evidence_calls: list[dict] = []
        self.generate_calls: list[dict] = []

    def evidence(self, **kwargs) -> dict:
        """Return citation-addressable stock and risk evidence."""
        self.evidence_calls.append(dict(kwargs))
        return {
            "run_id": self.run_id,
            "evidence_hash": "e" * 64,
            "data_gaps": [],
            "evidence_items": [
                {
                    "evidence_id": "E-DC-RANK-600519.SH",
                    "source": "data_center",
                    "observed_at": "2026-08-08T15:10:00+08:00",
                    "payload": {
                        "symbol": "600519.SH",
                        "name": "Kweichow Moutai",
                        "rank": 1,
                        "score": 88.0,
                    },
                },
                {
                    "evidence_id": "E-DC-FACTOR-600519.SH",
                    "source": "data_center",
                    "observed_at": "2026-08-08T15:10:00+08:00",
                    "payload": {"quality": 95.0, "growth": 85.0},
                },
                {
                    "evidence_id": "E-PR-RISK-PORTFOLIO",
                    "source": "portfolio_risk_center",
                    "observed_at": "2026-08-08T15:10:00+08:00",
                    "payload": {
                        "security_risks": [
                            {"symbol": "600519.SH", "risk_score": 32.0}
                        ]
                    },
                },
            ],
        }

    def generate(self, **kwargs) -> dict:
        """Return a bounded report carrying explicit non-execution flags."""
        self.generate_calls.append(dict(kwargs))
        return {
            "report_id": "copilot-report-test",
            "run_id": self.run_id,
            "evidence_ids": ["E-DC-RANK-600519.SH"],
            "evidence_hash": "e" * 64,
            "content": {"summary": "Evidence-grounded explanation"},
            "evaluation": {"grounded": True},
            "status": "published",
            "used_for_execution": False,
            "can_trade": False,
        }

    def reports(self, _limit: int) -> dict:
        """List no saved explanation so GET cannot trigger model generation."""
        return {"items": [], "can_trade": False}


class InvestmentOSReadFake:
    """Expose reports and notifications but no scheduler or trading commands."""

    def __init__(self) -> None:
        self.registry = SimpleNamespace(enabled=True)
        self.read_notifications: list[str] = []
        self.forbidden_calls: list[str] = []

    def reports(self, _report_type, _limit: int) -> dict:
        """Return an empty existing report list without generation."""
        return {"items": [], "can_trade": False, "can_create_orders": False}

    def report(self, report_id: str) -> dict:
        """Return one immutable report reference."""
        return {
            "report_id": report_id,
            "evidence": [{"evidence_id": "E-DC-MANIFEST"}],
            "can_trade": False,
            "can_create_orders": False,
        }

    def notification_items(self, _limit: int) -> dict:
        """Return one existing unread in-app notification."""
        return {
            "items": [
                {
                    "notification_id": "notification-test",
                    "status": "unread",
                    "level": "WARNING",
                    "can_trade": False,
                    "can_create_orders": False,
                }
            ],
            "can_trade": False,
            "can_create_orders": False,
        }

    def mark_notification_read(self, notification_id: str) -> dict:
        """Record only the acknowledgement identifier."""
        self.read_notifications.append(notification_id)
        return {
            "notification_id": notification_id,
            "status": "read",
            "can_trade": False,
            "can_create_orders": False,
        }

    def generate_report(self, *_args, **_kwargs) -> None:
        """Fail if a mobile endpoint tries to generate an operating report."""
        self.forbidden_calls.append("generate_report")
        raise AssertionError("mobile must not run operating jobs")

    def close(self) -> None:
        """Match the application lifespan without owning resources."""


def make_mobile_service(
    tmp_path: Path,
    *,
    formal: bool,
) -> tuple[
    MobileInvestmentService,
    DailyEvidenceFake,
    CopilotEvidenceFake,
    InvestmentOSReadFake,
]:
    """Compose the production mobile service from isolated non-trading fakes."""
    daily = DailyEvidenceFake(formal=formal)
    copilot = CopilotEvidenceFake()
    operating = InvestmentOSReadFake()
    journal = MobileJournalStore(tmp_path / "mobile-service.db")
    service = MobileInvestmentService(
        tmp_path,
        daily_service=daily,
        workbench_service=WorkbenchValuationFake(),
        copilot_service=copilot,
        investment_os_service=operating,
        journal_store=journal,
    )
    return service, daily, copilot, operating


def test_mobile_dashboard_uses_valuation_and_never_promotes_preview(
    tmp_path: Path,
) -> None:
    """Keep canonical assets untouched and refuse preview/cumulative-return fallbacks."""
    service, daily, _copilot, operating = make_mobile_service(tmp_path, formal=False)
    try:
        payload = service.dashboard()
        assert payload["asset_valuation"] == WorkbenchValuationFake.valuation
        assert payload["asset_valuation"]["equity"] == 999.0
        assert payload["daily_performance"]["availability"] == "unavailable"
        assert payload["daily_performance"]["daily_return"] is None
        assert payload["opportunities"] == []
        assert payload["opportunities_availability"] == "unavailable"
        assert payload["can_trade"] is False
        assert payload["can_create_orders"] is False
        assert daily.forbidden_calls == []
        assert operating.forbidden_calls == []
    finally:
        service.journal.close()


def test_mobile_stock_detail_is_formal_evidence_only_and_get_does_not_call_ai(
    tmp_path: Path,
) -> None:
    """Require formal run citations and keep a stock GET free of model side effects."""
    service, daily, copilot, operating = make_mobile_service(tmp_path, formal=True)
    try:
        payload = service.stock_detail("600519.SH")
        assert payload["availability"] == "available"
        assert payload["formal_run_id"] == copilot.run_id
        assert payload["ranking"]["score"] == 88.0
        assert payload["factor_analysis"]["quality"] == 95.0
        assert payload["risk_analysis"]["risk_score"] == 32.0
        assert payload["evidence_hash"] == "e" * 64
        assert {item["evidence_id"] for item in payload["evidence"]} >= {
            "E-DC-RANK-600519.SH",
            "E-DC-FACTOR-600519.SH",
        }
        assert copilot.evidence_calls[0]["run_id"] == copilot.run_id
        assert copilot.generate_calls == []
        assert daily.forbidden_calls == []
        assert operating.forbidden_calls == []
    finally:
        service.journal.close()


def test_mobile_copilot_and_notification_ack_are_audited_without_trading(
    tmp_path: Path,
) -> None:
    """Allow bounded admin actions while proving they never invoke trading workflows."""
    service, daily, copilot, operating = make_mobile_service(tmp_path, formal=True)
    try:
        response = service.chat(
            intent="why_selected",
            question="Why was this stock selected?",
            symbol="600519.SH",
            source_device_id="device-admin",
        )
        assert response["report_id"] == "copilot-report-test"
        assert response["used_for_execution"] is False
        assert response["can_affect_execution"] is False
        assert response["can_trade"] is False
        assert response["can_create_orders"] is False
        assert copilot.generate_calls == [
            {
                "report_type": "stock_analysis",
                "run_id": copilot.run_id,
                "symbol": "600519.SH",
                "trigger": "user_action",
            }
        ]
        assert service.journal.counts()["copilot_chats"] == 1

        read = service.mark_notification_read("notification-test")
        assert read["status"] == "read"
        assert operating.read_notifications == ["notification-test"]
        assert daily.forbidden_calls == []
        assert operating.forbidden_calls == []
    finally:
        service.journal.close()


class ApiRepositoryFake:
    """Satisfy the desktop app shell without creating backtest artifacts."""

    def latest_completed(self):
        """Return no unrelated desktop backtest."""
        return None


class ApiRunServiceFake:
    """Keep legacy state transitions observable if a security boundary regresses."""

    def __init__(self) -> None:
        self.repository = ApiRepositoryFake()
        self.calls: list[str] = []

    def shutdown(self) -> None:
        """Match the application lifespan contract."""

    def status(self) -> dict:
        """Return a harmless idle state."""
        return {"state": "idle"}

    def set_kill_switch(self, _enabled: bool) -> dict:
        """Record a forbidden legacy mutation if middleware ever lets it through."""
        self.calls.append("set_kill_switch")
        return {"enabled": True, "live_trading_enabled": False}


def make_mobile_api(
    tmp_path: Path,
    *,
    auth: MobileAuthService,
    service: MobileInvestmentService,
) -> tuple:
    """Build a mobile-enabled app with every business dependency injected."""
    web = tmp_path / "web" / "mobile"
    web.mkdir(parents=True, exist_ok=True)
    (web / "index.html").write_text("<h1>Pangu Mobile</h1>", encoding="utf-8")
    runs = ApiRunServiceFake()
    app = create_app(
        tmp_path,
        run_service=runs,
        model_service=ModelService(),
        daily_research_service=service.daily,
        workbench_service=service.workbench,
        copilot_service=service.copilot,
        investment_os_service=service.investment_os,
        investment_job_manager=SimpleNamespace(run=lambda *_args, **_kwargs: {}),
        mobile_enabled=True,
        mobile_auth_service=auth,
        mobile_investment_service=service,
        port=8765,
    )
    return app, runs


def issue_and_pair(
    app,
    *,
    role: str,
    remote_host: str = "192.168.10.55",
) -> dict:
    """Issue locally and consume remotely one role-locked pairing challenge."""
    local = TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    )
    challenge_response = local.post(
        "/api/v1/mobile/pairing-codes",
        json={"role": role},
        headers={
            "X-Ashare-Client": "local-dashboard",
            "Origin": "http://127.0.0.1:8765",
        },
    )
    assert challenge_response.status_code == 200, challenge_response.text
    challenge = challenge_response.json()
    remote = TestClient(
        app,
        base_url="http://192.168.10.2:8765",
        client=(remote_host, 50100),
    )
    paired_response = remote.post(
        "/api/mobile/v1/auth/pair",
        json={
            "pairing_id": challenge["pairing_id"],
            "pairing_code": challenge["pairing_code"],
            "device_name": f"{role}-api-phone",
            # An exchange request cannot choose or elevate the locked role.
            "role": "admin" if role == "viewer" else "viewer",
        },
    )
    assert paired_response.status_code == 200, paired_response.text
    paired = paired_response.json()
    assert paired["role"] == role
    return paired


def test_mobile_pairing_is_loopback_only_and_lan_static_is_accessible(
    tmp_path: Path,
) -> None:
    """Expose only mobile static/API routes to LAN and keep enrollment local-admin only."""
    service, _daily, _copilot, _operating = make_mobile_service(tmp_path, formal=False)
    auth = make_auth(MutableClock())
    app, _runs = make_mobile_api(tmp_path, auth=auth, service=service)
    try:
        remote = TestClient(
            app,
            base_url="http://192.168.10.2:8765",
            client=("192.168.10.55", 50100),
        )
        static = remote.get("/mobile/")
        assert static.status_code == 200
        assert "Pangu Mobile" in static.text
        forged = remote.post(
            "/api/v1/mobile/pairing-codes",
            json={"role": "admin"},
            headers={
                "X-Ashare-Client": "local-dashboard",
                "Origin": "http://127.0.0.1:8765",
            },
        )
        assert forged.status_code == 403

        local = TestClient(
            app,
            base_url="http://127.0.0.1:8765",
            client=("127.0.0.1", 50000),
        )
        assert local.post(
            "/api/v1/mobile/pairing-codes", json={"role": "viewer"}
        ).status_code == 403
    finally:
        service.journal.close()


def test_mobile_stock_without_formal_evidence_returns_schema_safe_unavailable(
    tmp_path: Path,
) -> None:
    """Return an honest 200/unavailable DTO instead of failing response validation."""
    service, _daily, _copilot, _operating = make_mobile_service(
        tmp_path, formal=False
    )
    auth = make_auth(MutableClock())
    app, _runs = make_mobile_api(tmp_path, auth=auth, service=service)
    try:
        viewer = issue_and_pair(app, role="viewer")
        remote = TestClient(
            app,
            base_url="http://192.168.10.2:8765",
            client=("192.168.10.55", 50100),
        )
        response = remote.get(
            "/api/mobile/v1/stocks/600519.SH",
            headers={"Authorization": f"Bearer {viewer['access_token']}"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["availability"] == "unavailable"
        assert payload["formal_run_id"] is None
        assert payload["used_for_execution"] is False
        assert payload["can_affect_execution"] is False
        assert payload["can_trade"] is False
        assert payload["can_create_orders"] is False
    finally:
        service.journal.close()


def test_mobile_roles_and_legacy_route_isolation(tmp_path: Path) -> None:
    """Permit viewer reads/admin audit writes while denying every legacy mutation."""
    service, daily, _copilot, operating = make_mobile_service(tmp_path, formal=True)
    auth = make_auth(MutableClock())
    app, runs = make_mobile_api(tmp_path, auth=auth, service=service)
    try:
        viewer = issue_and_pair(app, role="viewer")
        admin = issue_and_pair(app, role="admin", remote_host="192.168.10.56")
        viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
        admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
        remote = TestClient(
            app,
            base_url="http://192.168.10.2:8765",
            client=("192.168.10.55", 50100),
        )

        assert remote.get(
            "/api/mobile/v1/dashboard", headers=viewer_headers
        ).status_code == 200
        assert remote.get(
            "/api/mobile/v1/stocks/600519.SH", headers=viewer_headers
        ).status_code == 200

        chat_body = {
            "intent": "why_selected",
            "question": "Why was this stock selected?",
            "symbol": "600519.SH",
        }
        journal_body = {
            "idempotency_key": "00000000-0000-4000-8000-000000000701",
            "entry_type": "buy_reason",
            "trade_date": "2026-08-09",
            "symbol": "600519.SH",
            "title": "Evidence note",
            "content": "Saved for later review",
        }
        assert remote.post(
            "/api/mobile/v1/copilot/chat", json=chat_body, headers=viewer_headers
        ).status_code == 403
        assert remote.post(
            "/api/mobile/v1/journal", json=journal_body, headers=viewer_headers
        ).status_code == 403
        assert remote.post(
            "/api/mobile/v1/notifications/notification-test/read",
            headers=viewer_headers,
        ).status_code == 403
        assert remote.patch(
            "/api/mobile/v1/journal/nonexistent-entry",
            json={
                "idempotency_key": "00000000-0000-4000-8000-000000000702",
                "expected_version": 1,
                "content": "viewer must not write",
            },
            headers=viewer_headers,
        ).status_code == 403
        assert remote.request(
            "DELETE",
            "/api/mobile/v1/journal/nonexistent-entry",
            json={
                "idempotency_key": "00000000-0000-4000-8000-000000000703",
                "expected_version": 1,
            },
            headers=viewer_headers,
        ).status_code == 403

        chat = remote.post(
            "/api/mobile/v1/copilot/chat", json=chat_body, headers=admin_headers
        )
        assert chat.status_code == 200, chat.text
        assert chat.json()["can_create_orders"] is False
        journal = remote.post(
            "/api/mobile/v1/journal", json=journal_body, headers=admin_headers
        )
        assert journal.status_code == 200, journal.text
        assert journal.json()["can_affect_execution"] is False
        acknowledged = remote.post(
            "/api/mobile/v1/notifications/notification-test/read",
            headers=admin_headers,
        )
        assert acknowledged.status_code == 200, acknowledged.text

        legacy_headers = {
            **admin_headers,
            "X-Ashare-Client": "local-dashboard",
            "Origin": "http://127.0.0.1:8765",
        }
        for path, body in (
            ("/api/v1/paper/orders/preview", {}),
            ("/api/v1/model/deepseek/clear", None),
            ("/api/v1/safety/kill-switch", {"enabled": True}),
        ):
            response = remote.post(path, json=body, headers=legacy_headers)
            assert response.status_code == 403, (path, response.text)
        for path in (
            "/api/v1/status",
            "/api/v1/strategy",
            "/api/v1/workbench",
            "/api/v1/daily-research",
            "/api/v1/model/status",
        ):
            assert remote.get(path, headers=viewer_headers).status_code == 403, path
            assert remote.get(path, headers=admin_headers).status_code == 403, path

        local = TestClient(
            app,
            base_url="http://127.0.0.1:8765",
            client=("127.0.0.1", 50000),
        )
        assert local.post(
            "/api/v1/safety/kill-switch",
            json={"enabled": True},
            headers=admin_headers,
        ).status_code == 403
        assert runs.calls == []
        assert daily.forbidden_calls == []
        assert operating.forbidden_calls == []
    finally:
        service.journal.close()


def test_admin_mobile_journal_crud_remains_soft_deleted_and_audited(
    tmp_path: Path,
) -> None:
    """Exercise mobile journal CRUD through JWT routes without touching trading state."""
    service, daily, _copilot, operating = make_mobile_service(tmp_path, formal=True)
    auth = make_auth(MutableClock())
    app, _runs = make_mobile_api(tmp_path, auth=auth, service=service)
    try:
        admin = issue_and_pair(app, role="admin")
        headers = {"Authorization": f"Bearer {admin['access_token']}"}
        remote = TestClient(
            app,
            base_url="http://192.168.10.2:8765",
            client=("192.168.10.55", 50100),
        )
        created = remote.post(
            "/api/mobile/v1/journal",
            headers=headers,
            json={
                "idempotency_key": "00000000-0000-4000-8000-000000000711",
                "entry_type": "review",
                "trade_date": "2026-08-09",
                "title": "Closing review",
                "content": "Review the evidence, not the outcome alone.",
            },
        )
        assert created.status_code == 200, created.text
        entry_id = created.json()["entry_id"]
        updated = remote.patch(
            f"/api/mobile/v1/journal/{entry_id}",
            headers=headers,
            json={
                "idempotency_key": "00000000-0000-4000-8000-000000000712",
                "expected_version": 1,
                "content": "Updated only after checking the evidence chain.",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2
        archived = remote.request(
            "DELETE",
            f"/api/mobile/v1/journal/{entry_id}",
            headers=headers,
            json={
                "idempotency_key": "00000000-0000-4000-8000-000000000713",
                "expected_version": 2,
            },
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"
        detail = remote.get(
            f"/api/mobile/v1/journal/{entry_id}", headers=headers
        ).json()
        assert [item["operation"] for item in detail["revisions"]] == [
            "update",
            "archive",
        ]
        assert remote.get(
            "/api/mobile/v1/journal", headers=headers
        ).json()["items"] == []
        assert daily.forbidden_calls == []
        assert operating.forbidden_calls == []
    finally:
        service.journal.close()


def test_mobile_disabled_is_lazy_and_ignores_an_unused_short_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the desktop app bootable and avoid a mobile database until opt-in."""
    monkeypatch.setenv("PANGU_MOBILE_JWT_SECRET", "short-unused-secret")
    root = tmp_path / "desktop-only-project"
    (root / "web").mkdir(parents=True)
    (root / "web" / "index.html").write_text("<h1>Desktop</h1>", encoding="utf-8")
    runs = ApiRunServiceFake()
    daily = DailyEvidenceFake(formal=False)
    workbench = WorkbenchValuationFake()
    copilot = CopilotEvidenceFake()
    operating = InvestmentOSReadFake()
    database_path = root / "output" / "mobile_assistant.db"

    app = create_app(
        root,
        run_service=runs,
        model_service=ModelService(),
        daily_research_service=daily,
        workbench_service=workbench,
        copilot_service=copilot,
        investment_os_service=operating,
        investment_job_manager=SimpleNamespace(run=lambda *_args, **_kwargs: {}),
        mobile_enabled=False,
        port=8765,
    )
    assert database_path.exists() is False
    with TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/v1/status").status_code == 200
        auth_status = client.get("/api/mobile/v1/auth/status")
        assert auth_status.status_code == 200
        assert auth_status.json()["enabled"] is False
        assert client.get("/api/mobile/v1/dashboard").status_code == 503
    assert database_path.exists() is False


def test_mobile_api_rejects_missing_tampered_and_expired_bearer_tokens(
    tmp_path: Path,
) -> None:
    """Apply JWT verification at the HTTP boundary, not only in service unit tests."""
    service, _daily, _copilot, _operating = make_mobile_service(tmp_path, formal=True)
    clock = MutableClock()
    auth = make_auth(clock)
    app, _runs = make_mobile_api(tmp_path, auth=auth, service=service)
    try:
        paired = issue_and_pair(app, role="viewer")
        token = paired["access_token"]
        remote = TestClient(
            app,
            base_url="http://192.168.10.2:8765",
            client=("192.168.10.55", 50100),
        )
        assert remote.get("/api/mobile/v1/dashboard").status_code == 401

        pieces = token.split(".")
        pieces[2] = ("A" if pieces[2][0] != "A" else "B") + pieces[2][1:]
        tampered = remote.get(
            "/api/mobile/v1/dashboard",
            headers={"Authorization": f"Bearer {'.'.join(pieces)}"},
        )
        assert tampered.status_code == 401
        assert tampered.json()["detail"]["code"] == "MOBILE_TOKEN_INVALID"

        clock.advance(seconds=301)
        expired = remote.get(
            "/api/mobile/v1/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert expired.status_code == 401
        assert expired.json()["detail"]["code"] == "MOBILE_TOKEN_EXPIRED"
    finally:
        service.journal.close()


def test_mobile_openapi_generated_client_and_frontend_contracts_are_in_sync(
    tmp_path: Path,
) -> None:
    """Prevent handwritten mobile APIs, hidden asset math, or accidental trade controls."""
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "docs" / "openapi.json").read_text(encoding="utf-8"))
    generated = (root / "web" / "generated" / "client.js").read_text(
        encoding="utf-8"
    )
    mobile_js = (root / "web" / "mobile" / "mobile.js").read_text(
        encoding="utf-8"
    )
    mobile_html = (root / "web" / "mobile" / "index.html").read_text(
        encoding="utf-8"
    )
    runtime_schema = create_app(
        tmp_path,
        run_service=ApiRunServiceFake(),
        model_service=ModelService(),
        daily_research_service=DailyEvidenceFake(formal=False),
        workbench_service=WorkbenchValuationFake(),
        copilot_service=CopilotEvidenceFake(),
        investment_os_service=InvestmentOSReadFake(),
        investment_job_manager=SimpleNamespace(run=lambda *_args, **_kwargs: {}),
        mobile_enabled=False,
    ).openapi()

    operations = {
        "create_mobile_pairing_code": ("post", "/api/v1/mobile/pairing-codes"),
        "get_mobile_auth_status": ("get", "/api/mobile/v1/auth/status"),
        "pair_mobile_device": ("post", "/api/mobile/v1/auth/pair"),
        "get_mobile_session": ("get", "/api/mobile/v1/session"),
        "get_mobile_dashboard": ("get", "/api/mobile/v1/dashboard"),
        "get_mobile_portfolio": ("get", "/api/mobile/v1/portfolio"),
        "get_mobile_stock_detail": ("get", "/api/mobile/v1/stocks/{symbol}"),
        "create_mobile_copilot_chat": ("post", "/api/mobile/v1/copilot/chat"),
        "list_mobile_copilot_history": ("get", "/api/mobile/v1/copilot/history"),
        "list_mobile_reports": ("get", "/api/mobile/v1/reports"),
        "get_mobile_report": ("get", "/api/mobile/v1/reports/{report_id}"),
        "list_mobile_notifications": ("get", "/api/mobile/v1/notifications"),
        "mark_mobile_notification_read": (
            "post",
            "/api/mobile/v1/notifications/{notification_id}/read",
        ),
        "list_mobile_journal": ("get", "/api/mobile/v1/journal"),
        "create_mobile_journal_entry": ("post", "/api/mobile/v1/journal"),
        "get_mobile_journal_entry": (
            "get",
            "/api/mobile/v1/journal/{entry_id}",
        ),
        "update_mobile_journal_entry": (
            "patch",
            "/api/mobile/v1/journal/{entry_id}",
        ),
        "archive_mobile_journal_entry": (
            "delete",
            "/api/mobile/v1/journal/{entry_id}",
        ),
    }
    for operation_id, (method, path) in operations.items():
        operation = schema["paths"][path][method]
        runtime_operation = runtime_schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert runtime_operation["operationId"] == operation_id
        assert f'"{operation_id}"' in generated
        assert f'"path": "{path}"' in generated
        assert operation_id in mobile_js

    public_operations = {
        "create_mobile_pairing_code",
        "get_mobile_auth_status",
        "pair_mobile_device",
    }
    for operation_id, (method, path) in operations.items():
        if operation_id not in public_operations:
            assert schema["paths"][path][method]["security"] == [
                {"HTTPBearer": []}
            ]

    assert 'from "../generated/client.js"' in mobile_js
    assert "fetch(" not in mobile_js
    assert '"/api/' not in mobile_js
    assert "sessionStorage.setItem(TOKEN_KEY" in mobile_js
    assert "requestOptions.accessToken = state.token" in mobile_js
    assert 'init.headers.Authorization = `Bearer ${String(accessToken)}`' in generated

    # ValuationService remains the sole owner of cash/equity/PnL/drawdown math.
    assert "data.daily_performance || data.performance" not in mobile_js
    assert "firstValue(performance.net_pnl, valuation.pnl)" not in mobile_js
    assert "firstValue(performance.drawdown, valuation.drawdown)" not in mobile_js
    assert ".reduce(" not in mobile_js

    forbidden_operations = {
        "submit_paper_buy",
        "submit_paper_sell",
        "submit_broker_paper_order",
        "set_kill_switch",
        "configure_deepseek",
        "collect_daily_research",
        "execute_daily_paper_plan",
    }
    assert all(operation not in mobile_js for operation in forbidden_operations)
    html_lower = mobile_html.lower()
    assert "买入下单" not in mobile_html
    assert "卖出下单" not in mobile_html
    assert "实盘" not in mobile_html or "不能" in mobile_html
    assert "strategy-editor" not in html_lower
    assert "risk-editor" not in html_lower
