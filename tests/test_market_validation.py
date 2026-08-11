from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ashare_agent.market import load_market_data


SYMBOL = "510300.SH"


def write_bars(path: Path, **overrides) -> None:
    """Write one minimal daily-bar file with optional invalid fields."""
    row = {
        "date": "2026-01-05",
        "open": 3.0,
        "high": 3.2,
        "low": 2.9,
        "close": 3.1,
        "volume": 1000,
    }
    row.update(overrides)
    pd.DataFrame([row]).to_csv(path / "510300_SH.csv", index=False)


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_non_finite_prices_are_rejected(tmp_path: Path, bad_value: float) -> None:
    write_bars(tmp_path, close=bad_value)
    with pytest.raises(ValueError):
        load_market_data([SYMBOL], tmp_path)


def test_impossible_ohlc_relation_is_rejected(tmp_path: Path) -> None:
    write_bars(tmp_path, high=2.8)
    with pytest.raises(ValueError):
        load_market_data([SYMBOL], tmp_path)


def test_valid_bars_are_sorted(tmp_path: Path) -> None:
    rows = [
        {"date": "2026-01-06", "open": 3.1, "high": 3.2, "low": 3.0, "close": 3.15, "volume": 900},
        {"date": "2026-01-05", "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05, "volume": 1000},
    ]
    pd.DataFrame(rows).to_csv(tmp_path / "510300_SH.csv", index=False)
    result = load_market_data([SYMBOL], tmp_path)[SYMBOL]
    assert result.iloc[0]["date"].date().isoformat() == "2026-01-05"
