from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd


THS_HISTORICAL_PATH = "/api/fund/market/historical"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class MarketDataBundle:
    """Return validated bars together with their truthful acquisition state."""

    frames: dict[str, pd.DataFrame]
    provider: str
    cache_used: bool
    fetched_at: str
    completed_through: str


class ThsFinanceApiError(RuntimeError):
    """Represent a safe, credential-free business error from the THS API."""

    def __init__(self, code: int, message: str, request_id: str | None = None) -> None:
        self.code = code
        self.request_id = request_id
        suffix = f"（request_id={request_id}）" if request_id else ""
        super().__init__(f"同花顺金融数据API返回错误 code={code}：{message}{suffix}")


def validate_market_frame(
    symbol: str, frame: pd.DataFrame, minimum_rows: int = 1
) -> pd.DataFrame:
    """Normalize and strictly validate one daily OHLCV frame."""
    required = ["date", "open", "high", "low", "close", "volume"]
    if not set(required).issubset(frame.columns):
        raise ValueError(f"{symbol}行情字段不完整")
    normalized = frame[required].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    if normalized["date"].isna().any():
        raise ValueError(f"{symbol}存在无效交易日期")
    if normalized["date"].duplicated().any():
        raise ValueError(f"{symbol}存在重复交易日")
    numeric_columns = ["open", "high", "low", "close", "volume"]
    numeric = normalized[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError(f"{symbol}存在缺失或非有限行情值")
    if (numeric[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{symbol}存在非正价格")
    if (numeric["volume"] < 0).any():
        raise ValueError(f"{symbol}存在负成交量")
    if (
        (numeric["high"] < numeric[["open", "close"]].max(axis=1)).any()
        or (numeric["low"] > numeric[["open", "close"]].min(axis=1)).any()
        or (numeric["high"] < numeric["low"]).any()
    ):
        raise ValueError(f"{symbol}存在异常OHLC关系")
    normalized[numeric_columns] = numeric
    normalized = normalized.sort_values("date").reset_index(drop=True)
    if len(normalized) < minimum_rows:
        raise ValueError(f"{symbol}有效行情不足{minimum_rows}行")
    return normalized


def load_market_data(
    symbols: list[str], data_dir: Path, minimum_rows: int = 1
) -> dict[str, pd.DataFrame]:
    """Load and validate cached daily OHLCV data for every allowed symbol."""
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = data_dir / f"{symbol.replace('.', '_')}.csv"
        if not path.exists():
            raise FileNotFoundError(f"缺少行情文件：{path}")
        result[symbol] = validate_market_frame(
            symbol,
            pd.read_csv(path),
            minimum_rows=minimum_rows,
        )
    return result


class ThsFinanceDataProvider:
    """Fetch THS historical daily bars with strict validation and cache fallback."""

    provider_name = "ths_finance"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @staticmethod
    def _date_to_milliseconds(value: str) -> int:
        """Encode a calendar date as Asia/Shanghai midnight milliseconds."""
        parsed = date.fromisoformat(value)
        localized = datetime.combine(parsed, datetime.min.time(), tzinfo=SHANGHAI_TZ)
        return int(localized.timestamp() * 1000)

    @staticmethod
    def _bars_to_frame(
        symbol: str, items: list[dict[str, Any]], minimum_rows: int
    ) -> pd.DataFrame:
        """Map the documented PriceBarItem contract into the engine schema."""
        frame = pd.DataFrame(items)
        required = {
            "date_ms",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        }
        if not required.issubset(frame.columns):
            raise ValueError(f"{symbol}同花顺历史K线字段不完整")
        milliseconds = pd.to_numeric(frame["date_ms"], errors="coerce")
        if milliseconds.isna().any():
            raise ValueError(f"{symbol}同花顺历史K线日期无效")
        dates = (
            pd.to_datetime(milliseconds, unit="ms", utc=True)
            .dt.tz_convert(SHANGHAI_TZ)
            .dt.normalize()
            .dt.tz_localize(None)
        )
        normalized = pd.DataFrame(
            {
                "date": dates,
                "open": frame["open_price"],
                "high": frame["high_price"],
                "low": frame["low_price"],
                "close": frame["close_price"],
                "volume": frame["volume"],
            }
        )
        return validate_market_frame(symbol, normalized, minimum_rows=minimum_rows)

    @staticmethod
    def _write_cache(
        data_dir: Path,
        frames: dict[str, pd.DataFrame],
        manifest: dict[str, Any],
    ) -> None:
        """Atomically replace a complete validated cache and non-secret manifest."""
        data_dir.mkdir(parents=True, exist_ok=True)
        temporary_paths: list[tuple[Path, Path]] = []
        for symbol, frame in frames.items():
            target = data_dir / f"{symbol.replace('.', '_')}.csv"
            temporary = target.with_suffix(".csv.tmp")
            frame.to_csv(temporary, index=False, date_format="%Y-%m-%d")
            temporary_paths.append((temporary, target))
        manifest_target = data_dir / "data_manifest.json"
        manifest_temporary = manifest_target.with_suffix(".json.tmp")
        manifest_temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for temporary, target in temporary_paths:
            temporary.replace(target)
        manifest_temporary.replace(manifest_target)

    def _client_instance(self) -> Any:
        """Create the HTTP boundary only when a network fetch is required."""
        if self._client is None:
            self._client = httpx.Client(
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json"},
            )
        return self._client

    def _download_symbol(
        self,
        *,
        symbol: str,
        base_url: str,
        api_key: str,
        start_date: str,
        end_date: str,
        interval: str,
        timeout_seconds: int,
        minimum_rows: int,
    ) -> tuple[pd.DataFrame, str | None]:
        """Fetch one symbol without logging or persisting the credential."""
        response = self._client_instance().get(
            f"{base_url.rstrip('/')}{THS_HISTORICAL_PATH}",
            headers={"X-api-key": api_key},
            params={
                "thscode": symbol,
                "interval": interval,
                "start": self._date_to_milliseconds(start_date),
                "end": self._date_to_milliseconds(end_date),
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("同花顺金融数据API响应不是对象")
        code = payload.get("code")
        request_id = payload.get("request_id")
        if code != 0:
            raise ThsFinanceApiError(
                int(code) if isinstance(code, int) else -1,
                str(payload.get("message") or "未知错误"),
                str(request_id) if request_id else None,
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("item"), list):
            raise ValueError(f"{symbol}同花顺历史K线响应结构无效")
        return (
            self._bars_to_frame(symbol, data["item"], minimum_rows),
            str(request_id) if request_id else None,
        )

    def _cached_bundle(
        self,
        *,
        data_dir: Path,
        symbols: list[str],
        end_date: str,
        minimum_rows: int,
        max_staleness_days: int,
        fallback_error: str,
    ) -> MarketDataBundle:
        """Use cache only when source, coverage and every symbol remain valid."""
        manifest_path = data_dir / "data_manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"同花顺数据下载失败且没有可信缓存：{fallback_error}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("provider") != self.provider_name:
            raise RuntimeError(f"同花顺数据下载失败且缓存来源不可信：{fallback_error}")
        frames = load_market_data(symbols, data_dir, minimum_rows=minimum_rows)
        completed_through = min(
            frame["date"].max().date().isoformat() for frame in frames.values()
        )
        minimum_date = date.fromisoformat(end_date) - timedelta(days=max_staleness_days)
        if date.fromisoformat(completed_through) < minimum_date:
            raise RuntimeError(f"同花顺数据下载失败且缓存已过期：{fallback_error}")
        now = datetime.now(timezone.utc).isoformat()
        cached_manifest = {
            **manifest,
            "cache_used": True,
            "used_at": now,
            "fallback_error": fallback_error[:500],
            "completed_through": completed_through,
        }
        self._write_cache(data_dir, frames, cached_manifest)
        return MarketDataBundle(
            frames=frames,
            provider=self.provider_name,
            cache_used=True,
            fetched_at=str(manifest.get("fetched_at") or now),
            completed_through=completed_through,
        )

    def fetch(
        self,
        *,
        symbols: list[str],
        start_date: str,
        end_date: str,
        data_dir: Path,
        base_url: str = "https://fuyao.aicubes.cn",
        api_key_env: str = "THS_FINANCE_API_KEY",
        interval: str = "1d",
        adjust: str = "none",
        request_timeout_seconds: int = 20,
        minimum_rows: int = 120,
        allow_cached_on_error: bool = True,
        max_staleness_days: int = 10,
    ) -> MarketDataBundle:
        """Fetch the full whitelist as one logical, immutable data batch."""
        try:
            api_key = os.getenv(api_key_env, "").strip()
            if not api_key:
                raise RuntimeError(f"未设置环境变量{api_key_env}")
            frames: dict[str, pd.DataFrame] = {}
            request_ids: dict[str, str] = {}
            for symbol in symbols:
                frame, request_id = self._download_symbol(
                    symbol=symbol,
                    base_url=base_url,
                    api_key=api_key,
                    start_date=start_date,
                    end_date=end_date,
                    interval=interval,
                    timeout_seconds=request_timeout_seconds,
                    minimum_rows=minimum_rows,
                )
                frames[symbol] = frame
                if request_id:
                    request_ids[symbol] = request_id
            completed_through = min(
                frame["date"].max().date().isoformat() for frame in frames.values()
            )
            fetched_at = datetime.now(timezone.utc).isoformat()
            manifest = {
                "provider": self.provider_name,
                "source_api": THS_HISTORICAL_PATH,
                "base_url": base_url,
                "fetched_at": fetched_at,
                "cache_used": False,
                "start_date": start_date,
                "end_date": end_date,
                "completed_through": completed_through,
                "interval": interval,
                "adjust": adjust,
                "symbols": symbols,
                "rows": {symbol: len(frame) for symbol, frame in frames.items()},
                "request_ids": request_ids,
            }
            self._write_cache(data_dir, frames, manifest)
            return MarketDataBundle(
                frames=frames,
                provider=self.provider_name,
                cache_used=False,
                fetched_at=fetched_at,
                completed_through=completed_through,
            )
        except Exception as exc:
            if not allow_cached_on_error:
                raise
            return self._cached_bundle(
                data_dir=data_dir,
                symbols=symbols,
                end_date=end_date,
                minimum_rows=minimum_rows,
                max_staleness_days=max_staleness_days,
                fallback_error=str(exc),
            )
