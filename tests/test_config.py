from pathlib import Path

import pytest
import yaml

from ashare_agent.config import load_config


def write_config(path: Path, mode: str, live_enabled: bool) -> None:
    """Write the smallest configuration needed to exercise the safety gate."""
    path.write_text(
        yaml.safe_dump(
            {
                "environment": {
                    "mode": mode,
                    "live_trading_enabled": live_enabled,
                }
            }
        ),
        encoding="utf-8",
    )


def test_live_trading_is_blocked(tmp_path: Path) -> None:
    """The research build must reject an enabled live-trading flag."""
    config_path = tmp_path / "live.yaml"
    write_config(config_path, mode="paper", live_enabled=True)

    with pytest.raises(RuntimeError, match="禁止启用实盘"):
        load_config(config_path)


def test_non_paper_mode_is_blocked(tmp_path: Path) -> None:
    """The research build must reject every environment except paper mode."""
    config_path = tmp_path / "non-paper.yaml"
    write_config(config_path, mode="live", live_enabled=False)

    with pytest.raises(RuntimeError, match="只允许paper模式"):
        load_config(config_path)
