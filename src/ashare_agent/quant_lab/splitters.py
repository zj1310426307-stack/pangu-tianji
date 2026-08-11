from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Sequence

from .contracts import DateRange, sha256_json
from .exceptions import ContractError


@dataclass(frozen=True)
class WalkForwardFold:
    """Describe one chronological fold after purge and embargo have been applied."""

    fold_id: str
    train: DateRange
    validation: DateRange
    test: DateRange
    purge_days: int
    embargo_days: int


@dataclass(frozen=True)
class SplitManifest:
    """Record all fold boundaries and the untouched final holdout."""

    method: str
    folds: tuple[WalkForwardFold, ...]
    final_holdout: DateRange | None
    label_horizon_days: int
    selection_can_access_final_holdout: bool = False

    @property
    def manifest_hash(self) -> str:
        """Hash the exact chronology contract."""
        return sha256_json(asdict(self))


def _dates(values: Sequence[str | date]) -> list[date]:
    """Normalize a unique sorted trading-date sequence."""
    dates = sorted({item if isinstance(item, date) else date.fromisoformat(item) for item in values})
    if len(dates) < 3:
        raise ContractError("walk-forward至少需要3个不同日期")
    return dates


def _range(values: list[date]) -> DateRange:
    """Convert a non-empty date slice into an inclusive contract range."""
    if not values:
        raise ContractError("实验切分产生空区间")
    return DateRange(values[0].isoformat(), values[-1].isoformat())


def validate_label_overlap(manifest: SplitManifest) -> None:
    """Reject labels whose forward horizon leaks across adjacent fold boundaries."""
    horizon = timedelta(days=manifest.label_horizon_days)
    for fold in manifest.folds:
        train_end = date.fromisoformat(fold.train.end)
        validation_start = date.fromisoformat(fold.validation.start)
        validation_end = date.fromisoformat(fold.validation.end)
        test_start = date.fromisoformat(fold.test.start)
        if train_end + horizon >= validation_start:
            raise ContractError(f"{fold.fold_id}训练标签跨入验证区间")
        if validation_end + horizon >= test_start:
            raise ContractError(f"{fold.fold_id}验证标签跨入测试区间")
    if manifest.final_holdout and manifest.folds:
        last_test = date.fromisoformat(manifest.folds[-1].test.end)
        holdout_start = date.fromisoformat(manifest.final_holdout.start)
        if last_test + horizon >= holdout_start:
            raise ContractError("测试标签跨入final holdout")


class ExpandingWalkForward:
    """Generate folds with an expanding train window and fixed validation/test windows."""

    def split(
        self,
        dates: Sequence[str | date],
        *,
        minimum_train: int,
        validation_size: int,
        test_size: int,
        step_size: int | None = None,
        purge_days: int = 0,
        embargo_days: int = 0,
        label_horizon_days: int = 0,
        final_holdout_size: int = 0,
    ) -> SplitManifest:
        """Create deterministic chronological folds and preserve a final holdout."""
        values = _dates(dates)
        holdout = values[-final_holdout_size:] if final_holdout_size else []
        work = values[:-final_holdout_size] if final_holdout_size else values
        step = step_size or test_size
        folds: list[WalkForwardFold] = []
        cursor = minimum_train
        while cursor + purge_days + validation_size + embargo_days + test_size <= len(work):
            train = work[:cursor]
            validation_start = cursor + purge_days
            validation = work[validation_start:validation_start + validation_size]
            test_start = validation_start + validation_size + embargo_days
            test = work[test_start:test_start + test_size]
            folds.append(WalkForwardFold(
                f"fold-{len(folds)+1:03d}", _range(train), _range(validation), _range(test),
                purge_days, embargo_days,
            ))
            cursor += step
        if not folds:
            raise ContractError("日期不足，无法生成expanding walk-forward")
        manifest = SplitManifest(
            "expanding_walk_forward", tuple(folds), _range(holdout) if holdout else None,
            label_horizon_days,
        )
        validate_label_overlap(manifest)
        return manifest


class RollingWalkForward:
    """Generate folds with a fixed-size rolling train window."""

    def split(
        self,
        dates: Sequence[str | date],
        *,
        train_size: int,
        validation_size: int,
        test_size: int,
        step_size: int | None = None,
        purge_days: int = 0,
        embargo_days: int = 0,
        label_horizon_days: int = 0,
        final_holdout_size: int = 0,
    ) -> SplitManifest:
        """Create fixed-length folds without exposing future data."""
        values = _dates(dates)
        holdout = values[-final_holdout_size:] if final_holdout_size else []
        work = values[:-final_holdout_size] if final_holdout_size else values
        step = step_size or test_size
        folds: list[WalkForwardFold] = []
        start = 0
        needed = train_size + purge_days + validation_size + embargo_days + test_size
        while start + needed <= len(work):
            train = work[start:start + train_size]
            validation_start = start + train_size + purge_days
            validation = work[validation_start:validation_start + validation_size]
            test_start = validation_start + validation_size + embargo_days
            test = work[test_start:test_start + test_size]
            folds.append(WalkForwardFold(
                f"fold-{len(folds)+1:03d}", _range(train), _range(validation), _range(test),
                purge_days, embargo_days,
            ))
            start += step
        if not folds:
            raise ContractError("日期不足，无法生成rolling walk-forward")
        manifest = SplitManifest(
            "rolling_walk_forward", tuple(folds), _range(holdout) if holdout else None,
            label_horizon_days,
        )
        validate_label_overlap(manifest)
        return manifest


class FinalHoldout:
    """Guard an untouched final sample from model selection code paths."""

    def __init__(self, date_range: DateRange) -> None:
        date_range.validate("final_holdout")
        self.date_range = date_range

    def assert_selection_range(self, start: str, end: str) -> None:
        """Reject any training or selection request touching the final holdout."""
        candidate = DateRange(start, end)
        candidate.validate("selection")
        if date.fromisoformat(candidate.end) >= date.fromisoformat(self.date_range.start):
            raise ContractError("final holdout禁止用于训练、验证或参数选择")
