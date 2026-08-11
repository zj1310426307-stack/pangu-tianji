from __future__ import annotations

from typing import Any, Mapping

from ..contracts import sha256_json
from .contracts import (
    GateName,
    GateOutcome,
    GateStatus,
    ValidationEvidenceBundle,
    ValidationGateConfig,
)


def _check(code: str, passed: bool, actual: Any, expected: Any) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), "actual": actual, "expected": expected}


class ValidationRules:
    """Evaluate fixed admission rules without changing strategy or account state."""

    def __init__(self, config: ValidationGateConfig | None = None) -> None:
        self.config = config or ValidationGateConfig()

    def evaluate(self, bundle: ValidationEvidenceBundle) -> tuple[GateOutcome, ...]:
        """Run all five gates and expose unavailable evidence explicitly."""
        return (
            self.data_integrity(bundle),
            self.factor_validity(bundle),
            self.robustness(bundle),
            self.out_of_sample(bundle),
            self.paper_trading(bundle),
        )

    def data_integrity(self, bundle: ValidationEvidenceBundle) -> GateOutcome:
        evidence = dict(bundle.data_integrity or {})
        checks = (
            _check("POINT_IN_TIME", evidence.get("dataset_label") == "POINT_IN_TIME",
                   evidence.get("dataset_label"), "POINT_IN_TIME"),
            _check("VALIDATION_PASS", evidence.get("validation_status") == "PASS",
                   evidence.get("validation_status"), "PASS"),
            _check("NO_FUTURE_DATA", evidence.get("future_data_used") is False,
                   evidence.get("future_data_used"), False),
            _check("ARTIFACTS_VERIFIED", evidence.get("artifacts_verified") is True,
                   evidence.get("artifacts_verified"), True),
            _check("ASSET_HASHES_MATCH", evidence.get("asset_hashes_match") is True,
                   evidence.get("asset_hashes_match"), True),
            _check("POINT_IN_TIME_FIELDS", evidence.get("point_in_time_fields") is True,
                   evidence.get("point_in_time_fields"), True),
        )
        passed = all(item["passed"] for item in checks)
        reasons = (() if passed else ("REJECT_DATA",)) + tuple(
            item["code"] for item in checks if not item["passed"]
        )
        return GateOutcome(
            gate=GateName.DATA_INTEGRITY,
            status=GateStatus.PASSED if passed else GateStatus.BLOCKED,
            score=100.0 if passed else 0.0,
            summary="点时数据与不可变证据校验通过" if passed else "数据完整性证据不足，准入关闭",
            checks=checks,
            evidence_ids=self._ids(evidence),
            block_reasons=reasons,
        )

    def factor_validity(self, bundle: ValidationEvidenceBundle) -> GateOutcome:
        report = bundle.factor_report
        if not report:
            return self._unavailable(GateName.FACTOR_VALIDITY, "缺少008-B因子研究报告")
        ic = ((report.get("sections") or {}).get("03_ic_analysis") or {})
        qualifying = 0
        available = 0
        for horizons in ic.values():
            if not isinstance(horizons, Mapping):
                continue
            best = False
            for values in horizons.values():
                if not isinstance(values, Mapping):
                    continue
                if values.get("icir") is None or values.get("ic_hit_rate") is None:
                    continue
                available += 1
                if (
                    int(values.get("observation_count") or 0) >= self.config.minimum_factor_observations
                    and float(values["icir"]) > self.config.minimum_factor_icir
                    and float(values["ic_hit_rate"]) >= self.config.minimum_factor_hit_rate
                ):
                    best = True
            qualifying += int(best)
        quantiles = ((report.get("sections") or {}).get("04_quantile_returns") or {})
        monotonic_total = 0
        monotonic_pass = 0
        for counts in quantiles.values():
            if not isinstance(counts, Mapping):
                continue
            quintiles = counts.get("5") or counts.get(5) or {}
            for values in quintiles.values() if isinstance(quintiles, Mapping) else ():
                if isinstance(values, Mapping) and values.get("monotonic_low_to_high") is not None:
                    monotonic_total += 1
                    monotonic_pass += int(bool(values["monotonic_low_to_high"]))
        monotonic_ratio = monotonic_pass / monotonic_total if monotonic_total else None
        redundancy = ((report.get("sections") or {}).get("06_correlation_redundancy") or {})
        review_candidates = redundancy.get("review_candidates") or []
        checks = (
            _check("POINT_IN_TIME_FACTOR_REPORT", report.get("dataset_label") == "POINT_IN_TIME",
                   report.get("dataset_label"), "POINT_IN_TIME"),
            _check("FACTOR_SAMPLE_COVERAGE", available > 0, available, ">0"),
            _check("QUALIFYING_FACTORS", qualifying >= self.config.minimum_valid_factors,
                   qualifying, self.config.minimum_valid_factors),
            _check("QUANTILE_MONOTONICITY", monotonic_ratio is not None and
                   monotonic_ratio >= self.config.minimum_monotonic_factor_ratio,
                   monotonic_ratio, self.config.minimum_monotonic_factor_ratio),
            _check("REDUNDANCY_REVIEW", len(review_candidates) <=
                   self.config.maximum_redundancy_review_candidates,
                   len(review_candidates), self.config.maximum_redundancy_review_candidates),
        )
        passed = all(item["passed"] for item in checks)
        factor_ratio = min(1.0, qualifying / self.config.minimum_valid_factors)
        monotonic_component = float(monotonic_ratio or 0.0)
        score = round(100.0 * (0.7 * factor_ratio + 0.3 * monotonic_component), 6)
        return GateOutcome(
            gate=GateName.FACTOR_VALIDITY,
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=score,
            summary="因子统计与分层证据达到门槛" if passed else "因子有效性门槛未全部满足",
            checks=checks,
            evidence_ids=self._ids(report),
            block_reasons=tuple(item["code"] for item in checks if not item["passed"]),
        )

    def robustness(self, bundle: ValidationEvidenceBundle) -> GateOutcome:
        report = bundle.robustness_report
        if not report:
            return self._unavailable(GateName.ROBUSTNESS, "缺少008-C稳健性研究报告")
        section = ((report.get("sections") or {}).get("10_robustness_score") or {})
        score = section.get("score")
        checks = (
            _check("POINT_IN_TIME_ROBUSTNESS", report.get("dataset_label") == "POINT_IN_TIME",
                   report.get("dataset_label"), "POINT_IN_TIME"),
            _check("ROBUSTNESS_AVAILABLE", section.get("availability") == "AVAILABLE",
                   section.get("availability"), "AVAILABLE"),
            _check("ROBUSTNESS_SCORE", score is not None and
                   float(score) >= self.config.minimum_robustness_score,
                   score, self.config.minimum_robustness_score),
            _check("NO_PARAMETER_AUTO_SELECTION",
                   ((report.get("sections") or {}).get("11_research_conclusion") or {})
                   .get("best_parameter_selected") is False,
                   ((report.get("sections") or {}).get("11_research_conclusion") or {})
                   .get("best_parameter_selected"), False),
        )
        passed = all(item["passed"] for item in checks)
        return GateOutcome(
            gate=GateName.ROBUSTNESS,
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=float(score) if score is not None else 0.0,
            summary="压力、滚动与过拟合证据达到门槛" if passed else "稳健性门槛未全部满足",
            checks=checks,
            evidence_ids=self._ids(report),
            block_reasons=tuple(item["code"] for item in checks if not item["passed"]),
        )

    def out_of_sample(self, bundle: ValidationEvidenceBundle) -> GateOutcome:
        evidence = bundle.out_of_sample
        if not evidence:
            return self._unavailable(GateName.OUT_OF_SAMPLE, "缺少锁定样本外证据")
        observation_count = int(evidence.get("observation_count") or 0)
        sharpe = evidence.get("sharpe")
        drawdown = evidence.get("max_drawdown")
        checks = (
            _check("POINT_IN_TIME_OOS", evidence.get("dataset_label") == "POINT_IN_TIME",
                   evidence.get("dataset_label"), "POINT_IN_TIME"),
            _check("HOLDOUT_LOCKED", evidence.get("holdout_locked_before_evaluation") is True,
                   evidence.get("holdout_locked_before_evaluation"), True),
            _check("HOLDOUT_NOT_USED_FOR_SELECTION", evidence.get("used_for_parameter_selection") is False,
                   evidence.get("used_for_parameter_selection"), False),
            _check("OOS_OBSERVATIONS", observation_count >= self.config.minimum_oos_observations,
                   observation_count, self.config.minimum_oos_observations),
            _check("OOS_SHARPE", sharpe is not None and
                   float(sharpe) >= self.config.minimum_oos_sharpe,
                   sharpe, self.config.minimum_oos_sharpe),
            _check("OOS_DRAWDOWN", drawdown is not None and
                   abs(float(drawdown)) <= self.config.maximum_oos_drawdown,
                   drawdown, self.config.maximum_oos_drawdown),
            _check("OOS_ARTIFACT_VERIFIED", evidence.get("artifact_verified") is True,
                   evidence.get("artifact_verified"), True),
        )
        passed = all(item["passed"] for item in checks)
        score = 100.0 * sum(int(item["passed"]) for item in checks) / len(checks)
        return GateOutcome(
            gate=GateName.OUT_OF_SAMPLE,
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=round(score, 6),
            summary="独立锁定样本外证据达到门槛" if passed else "样本外验证门槛未全部满足",
            checks=checks,
            evidence_ids=self._ids(evidence),
            block_reasons=tuple(item["code"] for item in checks if not item["passed"]),
        )

    def paper_trading(self, bundle: ValidationEvidenceBundle) -> GateOutcome:
        evidence = bundle.paper_trading
        if not evidence:
            return self._unavailable(GateName.PAPER_TRADING, "尚无模拟运行证据")
        trading_days = int(evidence.get("trading_days") or 0)
        deviation = evidence.get("execution_deviation")
        checks = (
            _check("PAPER_DAYS", trading_days >= self.config.minimum_paper_trading_days,
                   trading_days, self.config.minimum_paper_trading_days),
            _check("PAPER_PERFORMANCE_RECORDED",
                   evidence.get("total_return") is not None and
                   evidence.get("max_drawdown") is not None,
                   {
                       "total_return": evidence.get("total_return"),
                       "max_drawdown": evidence.get("max_drawdown"),
                   }, "both metrics present"),
            _check("EXECUTION_DEVIATION", deviation is not None and
                   abs(float(deviation)) <= self.config.maximum_execution_deviation,
                   deviation, self.config.maximum_execution_deviation),
            _check("NO_UNKNOWN_ORDERS", int(evidence.get("unknown_order_count") or 0) == 0,
                   evidence.get("unknown_order_count"), 0),
            _check("NO_RECONCILIATION_GAPS",
                   int(evidence.get("reconciliation_gap_count") or 0) == 0,
                   evidence.get("reconciliation_gap_count"), 0),
            _check("PAPER_ONLY", evidence.get("live_orders_created") is False,
                   evidence.get("live_orders_created"), False),
            _check("ARTIFACT_VERIFIED", evidence.get("artifact_verified") is True,
                   evidence.get("artifact_verified"), True),
        )
        passed = all(item["passed"] for item in checks)
        score = 100.0 * sum(int(item["passed"]) for item in checks) / len(checks)
        return GateOutcome(
            gate=GateName.PAPER_TRADING,
            status=GateStatus.PASSED if passed else GateStatus.FAILED,
            score=round(score, 6),
            summary="模拟执行质量达到门槛，但不构成实盘许可" if passed else "模拟执行证据未达到门槛",
            checks=checks,
            evidence_ids=self._ids(evidence),
            block_reasons=tuple(item["code"] for item in checks if not item["passed"]),
        )

    @staticmethod
    def _unavailable(gate: GateName, reason: str) -> GateOutcome:
        return GateOutcome(
            gate=gate,
            status=GateStatus.UNAVAILABLE,
            score=None,
            summary=reason,
            checks=(),
            evidence_ids=(),
            block_reasons=(reason,),
        )

    @staticmethod
    def _ids(evidence: Mapping[str, Any]) -> tuple[str, ...]:
        values: list[str] = []
        for key in ("run_id", "report_id", "evidence_id", "dataset_version"):
            value = evidence.get(key)
            if value:
                values.append(f"{key}:{value}")
        for value in evidence.get("evidence_ids") or ():
            values.append(str(value.get("id") if isinstance(value, Mapping) else value))
        if not values:
            values.append(f"content_hash:{sha256_json(evidence)}")
        return tuple(dict.fromkeys(values))
