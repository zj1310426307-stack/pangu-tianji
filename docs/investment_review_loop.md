# PANGU-V3.2-003 Investment Review Loop

## 定位

Investment Review Loop 是 Personal Investment OS 内的个人复盘闭环，不是新的交易、研究或AI系统。它复用既有投资日志、Workbench模拟账本、正式 ResearchPipeline 结果、Portfolio Risk 和 Exit 证据。

```text
用户原话 Journal
        ↓
T+5 / T+20 / 自定义到期日
        ↓
站内 Reminder
        ↓
只读事实草稿（不保存）
        ↓
用户确认 Review
        ↓
可选 Lesson（仍需用户确认）
```

## 数据模型

- Journal 类型固定为 `OBSERVE / DECISION / REVIEW / LESSON`；用户原话和AI摘要分字段保存。
- Review 保存原逻辑状态、风险状态、事实变化、结果、错误、做得好的地方与 Evidence 引用。
- Reminder 类型固定为 `DAILY_REVIEW / REVIEW_DUE / RISK_CHANGED / RESEARCH_CHANGED`；状态固定为 `OPEN / READ / DISMISSED / DONE`。
- 复盘草稿不落库；只有用户确认后才保存 Review 和可选 Lesson。
- BUY/SELL 事实仍只来自 MockBroker 成交同步，日志不得复制或改写成交价格、数量、费用与盈亏。

## 提醒语义

- 提醒只比较已保存的权威快照，不启动研究、不调用模型、不撮合、不下单。
- 同一事实与日期使用业务去重键，同步重入不会产生重复提醒。
- `RISK_CHANGED` 与 `RESEARCH_CHANGED` 只在新的权威快照相对历史证据发生变化时生成。
- 当前仅支持站内提醒；不接入邮件、短信、微信或App Push。

## 实时账户贯通

投资驾驶舱、研究页、我的持仓、投资复盘、Daily Investment OS 与 Personal OS 都通过 Workbench/ValuationService 读取同一份 MockBroker 持仓。默认15秒轮询；模拟账户发生成功状态变更时，驾驶舱缓存立即失效。

`max_positions=0` 只关闭持仓只数上限。以下规则继续生效：

- 单股目标仓位15%，总仓位60%；
- 买入100股整手，卖出只允许整手或一次性清仓零股尾数；
- A股T+1，当日买入数量不可卖，下一交易日自动释放；
- 活动卖单冻结可卖股份，活动买单冻结资金；
- 不能超卖、做空、绕过停牌/涨跌停、回撤或 Kill Switch；
- UNKNOWN状态不自动重发。

次日账户检查固定覆盖全部实际持仓，不再截断为5只；正式研究目标作为新增候选合并展示，但是否买入仍由现金、仓位、风险和有效执行窗口决定。

## API与安全

复盘入口：

- `GET /api/v1/review`
- `POST/GET /api/v1/review/journal`
- `GET /api/v1/review/{journal_id}/draft`
- `POST /api/v1/review/{journal_id}`
- `GET/POST /api/v1/review/reminders`
- `POST /api/v1/review/reminders/{reminder_id}/status`

前端仅使用OpenAPI生成客户端。所有Journal、Review、Reminder表在数据库层固定：

```text
can_trade=false
can_create_orders=false
can_modify_strategy=false
can_modify_risk=false
```

该闭环不能修改 ResearchPipeline、因子、组合、风控或实盘状态，也不能替用户自动保存投资经验。
