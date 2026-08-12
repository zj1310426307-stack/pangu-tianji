"""Versioned deterministic alert catalog without remediation actions."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import AlertSeverity


@dataclass(frozen=True)
class AlertRule:
    rule_id: str
    severity: AlertSeverity
    message: str
    recommended_manual_action: str


DEFAULT_ALERT_RULES = (
    AlertRule("data-intelligence-error", AlertSeverity.ERROR, "Data Intelligence 出现 ERROR/BLOCKED", "在数据中心查看事件与证据，不要绕过门禁"),
    AlertRule("formal-research-missing", AlertSeverity.WARNING, "正式 Research run 超过配置窗口仍缺失", "确认交易日与任务历史，手工决定是否重新研究"),
    AlertRule("latest-data-stale", AlertSeverity.WARNING, "Data Center latest.json 已过期", "检查数据采集与正式研究记录"),
    AlertRule("sqlite-quick-check", AlertSeverity.CRITICAL, "SQLite quick_check 失败", "停止相关工程写入并从已验证备份人工恢复"),
    AlertRule("sqlite-lock-increase", AlertSeverity.ERROR, "SQLite lock/busy 异常增加", "检查长事务、文件锁和并发写入"),
    AlertRule("backup-stale-or-invalid", AlertSeverity.ERROR, "最近备份过期或校验失败", "显式创建备份并核对 manifest/hash"),
    AlertRule("job-heartbeat-stale", AlertSeverity.ERROR, "Job 心跳过期", "检查关联 Trace 和日志；系统不会自动重跑"),
    AlertRule("api-error-or-latency", AlertSeverity.WARNING, "API 5xx 或 p95 延迟越过阈值", "查看 operationId、Trace 和数据库状态"),
    AlertRule("quant-artifact-invalid", AlertSeverity.ERROR, "Quant Lab artifact 校验失败", "隔离该实验并人工核对封存证据"),
    AlertRule("strategy-version-drift", AlertSeverity.CRITICAL, "策略或因子版本漂移", "停止发布研究并核对版本清单；不要自动切换版本"),
    AlertRule("ai-explicit-failures", AlertSeverity.WARNING, "显式 AI 调用连续失败", "检查模型配置和关联报告；不要自动调用模型"),
    AlertRule("engineering-unhealthy", AlertSeverity.CRITICAL, "Engineering Health 进入 UNHEALTHY", "查看四维健康证据并人工处理工程故障"),
)
