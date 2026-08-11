from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest

from ashare_agent.market import ThsFinanceApiError, ThsFinanceDataProvider


def price_items(rows: int = 140, start: date = date(2026, 1, 1)) -> list[dict]:
    items = []
    for index in range(rows):
        day = start + timedelta(days=index)
        milliseconds = int(
            datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
            * 1000
        )
        close = 4.0 + index * 0.01
        items.append(
            {
                "date_ms": milliseconds,
                "open_price": close - 0.01,
                "high_price": close + 0.03,
                "low_price": close - 0.03,
                "close_price": close,
                "volume": 1_000_000 + index,
                "turnover": 4_000_000 + index,
            }
        )
    return items


def success_client(secret: str) -> tuple[httpx.Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-api-key"] == secret
        assert request.url.path == "/api/fund/market/historical"
        assert request.url.params["interval"] == "1d"
        assert "adjust" not in request.url.params
        return httpx.Response(
            200,
            request=request,
            json={
                "code": 0,
                "message": "success",
                "request_id": "request-safe-id",
                "data": {
                    "timestamp": 0,
                    "thscode": "510300.SH",
                    "interval": "1d",
                    "adjust": None,
                    "item": price_items(),
                },
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def test_ths_history_is_mapped_cached_and_never_persists_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "secret-value-must-not-be-written"
    monkeypatch.setenv("THS_FINANCE_API_KEY", secret)
    client, requests = success_client(secret)
    provider = ThsFinanceDataProvider(client)
    bundle = provider.fetch(
        symbols=["510300.SH", "159915.SZ"],
        start_date="2026-01-01",
        end_date="2026-05-20",
        data_dir=tmp_path,
        minimum_rows=120,
        allow_cached_on_error=False,
    )

    assert bundle.provider == "ths_finance"
    assert bundle.cache_used is False
    assert len(requests) == 2
    assert list(bundle.frames["510300.SH"].columns) == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    manifest = json.loads((tmp_path / "data_manifest.json").read_text("utf-8"))
    assert manifest["provider"] == "ths_finance"
    assert manifest["source_api"] == "/api/fund/market/historical"
    assert manifest["adjust"] == "none"
    assert manifest["request_ids"]["510300.SH"] == "request-safe-id"
    all_cache_text = "\n".join(
        path.read_text("utf-8") for path in tmp_path.iterdir() if path.is_file()
    )
    assert secret not in all_cache_text


def test_business_auth_error_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THS_FINANCE_API_KEY", "invalid")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "code": 2001,
                "message": "API key missing or invalid",
                "request_id": "auth-failure",
                "data": None,
            },
        )

    provider = ThsFinanceDataProvider(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ThsFinanceApiError, match="code=2001"):
        provider.fetch(
            symbols=["510300.SH"],
            start_date="2026-01-01",
            end_date="2026-05-20",
            data_dir=tmp_path,
            minimum_rows=120,
            allow_cached_on_error=False,
        )


def test_missing_key_can_only_use_fresh_ths_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "temporary-secret"
    monkeypatch.setenv("THS_FINANCE_API_KEY", secret)
    client, _ = success_client(secret)
    provider = ThsFinanceDataProvider(client)
    provider.fetch(
        symbols=["510300.SH"],
        start_date="2026-01-01",
        end_date="2026-05-20",
        data_dir=tmp_path,
        minimum_rows=120,
        allow_cached_on_error=False,
    )
    monkeypatch.delenv("THS_FINANCE_API_KEY")

    cached = ThsFinanceDataProvider().fetch(
        symbols=["510300.SH"],
        start_date="2026-01-01",
        end_date="2026-05-20",
        data_dir=tmp_path,
        minimum_rows=120,
        max_staleness_days=10,
    )
    assert cached.cache_used is True
    assert cached.provider == "ths_finance"

    manifest_path = tmp_path / "data_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["provider"] = "other_provider"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="来源不可信"):
        ThsFinanceDataProvider().fetch(
            symbols=["510300.SH"],
            start_date="2026-01-01",
            end_date="2026-05-20",
            data_dir=tmp_path,
            minimum_rows=120,
        )
