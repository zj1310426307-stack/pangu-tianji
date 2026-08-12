# Personal AI Investment Assistant

版本：`personal-ai-assistant-v1.0.0`

## 产品边界

个人AI投资助手只回答五类固定问题，并且只在用户显式点击“查询证据”后运行：

| intent | 用户问题 | 主要证据 |
|---|---|---|
| `daily_attention` | 今天该关注什么？ | Dashboard、正式Research run、晨报证据 |
| `portfolio_analysis` | 我的组合风险如何？ | Portfolio Snapshot、Portfolio Risk |
| `stock_reason` | 为什么关注这只股票？ | 股票排名、因子、风险与退出证据 |
| `portfolio_fit` | 这只股票适合我的组合吗？ | 个股证据、当前组合风险、Investment Profile |
| `behavior_review` | 最近有哪些投资行为需要复盘？ | Investment Journal、Review History、个人教练证据 |

没有正式收盘run时，前四类问题返回 `unavailable` 和“等待正式收盘run”，不使用盘中预览生成正式结论。行为复盘可以使用Personal OS已保存事件和日志，但缺证据必须明确列入`uncertainties`。

## 统一回答合同

```json
{
  "summary": "一句话结论",
  "key_points": ["关键依据"],
  "risks": ["风险"],
  "uncertainties": ["证据缺口"],
  "evidence_refs": [{
    "evidence_id": "E-...",
    "source": "data_center",
    "data_time": "...",
    "run_id": "...",
    "report_id": "...",
    "strategy_version": "...",
    "factor_version": "..."
  }],
  "suggested_next_action": "继续观察"
}
```

`suggested_next_action`只能是：`继续观察`、`查看风险详情`、`完成复盘`、`等待正式收盘run`、`记录投资理由`。模型文本出现“建议买入/卖出/加仓/减仓/清仓/下单”等交易导向措辞时，服务端隐藏该段并登记不确定性。

## 只读证据白名单

```text
get_dashboard_overview
get_research_evidence
get_stock_factor_evidence
get_portfolio_snapshot
get_portfolio_risk
get_exit_evidence
get_investment_profile
get_investment_journal
get_review_history
```

助手无研究、回测、实验、策略晋级、风控修改、组合修改或订单工具。统一安全能力固定为：

```text
used_for_execution=false
can_affect_execution=false
can_trade=false
can_create_orders=false
can_modify_strategy=false
can_modify_factor_weights=false
can_modify_portfolio=false
can_modify_risk=false
can_launch_experiment=false
can_auto_remediate=false
```

## API与前端边界

- 唯一新增接口：`POST /api/v1/assistant/query`；
- 请求通过本机写保护，不支持移动JWT或局域网访问旧桌面API；
- 网页只调用OpenAPI生成客户端的`query_personal_ai_assistant`，不手写URL或`fetch`；
- 前端只展示服务端结果，不计算资产、收益、仓位、回撤、风险或评分；
- 高级AI报告、策略实验、数据中心、策略进化、工程健康和可观测能力保留，但仅从设置页进入。

## 已知证据缺口

- 当前没有正式Market Regime、指数趋势、成交量或市场赚钱效应点时证据；
- 没有正式收盘run时无法生成前四类正式AI回答；
- 没有历史持仓风险序列时，持仓页显示“暂无历史风险序列”；
- 当前尚未建立个股与个人画像的点时适配评分，`portfolio_fit`只解释证据与缺口；
- 行为复盘质量取决于用户是否持续记录理由、结果与教训。
