# PANGU-V3.2 Investment Dashboard

`GET /api/v1/dashboard/overview` 是桌面首页的唯一数据入口。浏览器不再并行拼装账户、风险、研究和AI接口，也不计算资产、收益、仓位或回撤。

## 服务结构

```text
ValuationService → WorkbenchService ─┐
ResearchPipeline → DailyResearch ────┤
Portfolio & Risk Center → Daily OS ──┤
AI Copilot published reports ────────┤→ InvestmentDashboardService → 15秒Snapshot → Dashboard UI
Personal OS + Daily OS tasks ────────┘
```

`InvestmentDashboardService` 只是只读编排层：

- 资产原样读取 `asset_valuation`；
- 风险原样读取 `risk_assessment`、风险标记和退出信号；
- 股票分数、理由和风险标签来自统一 Research Pipeline；
- AI只读取与当前 `run_id` 匹配、状态为 `published` 且含Evidence引用的已有报告；
- 今日任务来自 Daily Investment OS 与 Personal OS；
- GET不会生成模型报告、重算因子、启动研究、撮合模拟订单或修改个人记录。

## Snapshot合同

每个进程内快照默认缓存15秒并包含：

- `snapshot_id`
- `created_at` / `expires_at`
- `valuation_version`
- `risk_version`
- `ai_report_id`
- `research.run_id` / `source_mode`

缓存不是新的投资事实库。过期后服务重新读取各权威服务；返回值经过深复制，调用方不能污染缓存。

成功的模拟买入、卖出、委托、撤单、撮合、自动执行、持仓监控或 Kill Switch 变更会让缓存立即失效。下一次读取从 Workbench 的 MockBroker 账本重新获取全部实际持仓；Portfolio Risk 只提供风险注释，不能替换持仓事实。

`watchlist` 不再截断为前5条，展示当前正式研究或盘中预览中全部已有评分记录。`next_session_plan` 来自 Workbench，范围固定为 `all_actual_positions`，不会因展示上限遗漏第6只及之后的持仓。

## 证据降级

- 没有Market Regime点时证据时，市场卡只显示研究池状态，并明确禁止声称牛市、震荡或熊市。
- 没有正式run或AI报告不匹配时，AI卡显示不可用；GET不调用DeepSeek。
- 没有单股风险或历史变化证据时，持仓健康保留空值与`data_gaps`，不推测健康变化。
- 没有正式收盘研究时可以展示盘中预览，但卡片固定标注“只读观察”和`used_for_execution=false`。

## 前端边界

七类组件位于 `web/components/dashboard/`：MarketCard、AssetCard、RiskCard、AICard、StockCard、HoldingHealthCard、TaskCard。组件只接受后端字段并格式化；所有请求经OpenAPI生成客户端。

安全能力在服务返回和DTO固定为：

```text
can_trade=false
can_create_orders=false
can_modify_strategy=false
can_modify_portfolio=false
can_modify_risk=false
```
