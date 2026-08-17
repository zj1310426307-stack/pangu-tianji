# 盘古·天机模型研究接口说明

## 设计目标

模型是可拔插、默认关闭、失败隔离的“研究解释器”，不是交易决策器。默认 `DisabledModelProvider` 零网络运行；启用时只允许一个成本型模型请求并发执行。网页当前直接支持 DeepSeek V4 Flash/Pro，AI Investment Copilot 通过 Evidence Reader 和发布前校验建立在这层提供商之上。

## 网页配置

在本机 `http://127.0.0.1:8765` 的“模型调用接口”中输入 DeepSeek API Key，选择模型并点击“保存并连接”。服务端先调用固定的 `https://api.deepseek.com/models` 验证Key和模型，验证成功后才写入当前Windows用户环境并立即热重载，无需重新启动。

- 请求只接受以 `sk-` 开头且长度受限的Key；
- Base URL固定为DeepSeek官方地址，网页不能提交任意目标地址；
- 模型只允许 `deepseek-v4-flash` 和 `deepseek-v4-pro`；
- Key字段使用 `SecretStr`，响应、OpenAPI、日志和网页状态不回显Key；
- 所有配置写操作继续受本机请求头、Host和Origin校验保护；
- “断开并清除Key”只删除盘古·天机的五个模型环境变量。

### 一次配置，全局共享

网页 DeepSeek 接入口是系统唯一的 AI 配置入口。验证成功后，当前服务进程中的同一个 `ModelService` 会立即热重载；回测解释、Top 10 解读、模拟持仓复盘、专业 AI 报告、投资运营报告、AI 量化研究、个人 AI 助手和移动投资助手都复用该实例。Windows 计划任务在下一次启动时从同一组当前用户环境变量读取配置，不需要为模块重复保存 Key。

`GET /api/v1/model/status` 通过 `runtime_scope=shared_system` 和 `consumer_modules` 返回脱敏的共享范围。该范围只表示模块可以调用同一模型，不授予 AI 修改评分、策略、风控、组合或订单的权限；模型调用仍由用户动作或已登记的只读报告任务显式触发。

服务重启后，已保存的 Key 状态为 `checking`（已配置、待检测），而不是错误地回到“未配置”。所有模块可直接响应下一次显式 AI 请求；成功完成后共享状态更新为 `connected`，失败则统一更新为脱敏的 `error`。启动过程本身不调用收费模型。

## 服务端环境变量

| 名称 | 必填 | 说明 |
|---|---:|---|
| `ASHARE_MODEL_PROVIDER` | 是 | 当前只识别 `openai_compatible` |
| `ASHARE_MODEL_BASE_URL` | 是 | 仅http/https，不得含账号、密码、query或fragment |
| `ASHARE_MODEL_API_KEY` | 是 | 保存到当前Windows用户环境；不进入项目文件 |
| `ASHARE_MODEL_NAME` | 是 | 提供商模型名称 |
| `ASHARE_MODEL_HEALTH_PATH` | 否 | 默认 `/models` |

缺少任一必填项时自动回退到禁用提供商。

## 输入合同

回测解释只接收：

```json
{
  "run_id": "uuid",
  "completed_at": "ISO-8601",
  "data_source": "synthetic",
  "metrics": {},
  "strategy_summary": {
    "symbols": [],
    "strategy": {},
    "risk": {},
    "model_in_execution": false
  }
}
```

`strategy_summary`来自该次运行的哈希校验配置快照。

每日研究解释只接收最新Top 10确定性排名、评分拆解、风险标签和经过裁剪的模拟账户摘要，不包含同花顺Key、DeepSeek Key、数据库连接或订单方法。

## 输出合同

兼容端点的assistant内容必须是JSON对象：

```json
{
  "summary": "对本次回测的客观解释",
  "risks": ["数据为合成行情", "结果不代表未来表现"]
}
```

服务端限制响应体、摘要长度、风险项数量和单项长度，并强制加入：

```json
{
  "used_for_execution": false
}
```

网络、超时、坏JSON或协议结构错误会返回隔离的模型错误，不会改变运行快照、策略、风控或订单。

DeepSeek V4调用显式使用非思考模式、JSON输出和有界输出长度，避免长思考阻塞本地控制台。主要接口：

- `POST /api/v1/model/deepseek/config`：验证、保存并热重载；
- `POST /api/v1/model/deepseek/clear`：断开并清除；
- `GET /api/v1/model/status`：返回脱敏状态；
- `POST /api/v1/model/connection-test`：重新探测 `/models`；
- `POST /api/v1/model/daily-research-explanation`：解释当前Top 10；
- `POST /api/v1/model/explanations`：解释已完成回测快照。

## AI Investment Copilot 合同

Copilot 不允许 Agent 自行读数据库。`EvidenceReader` 是唯一证据入口，只通过 Data Center 公共读取方法和 Portfolio & Risk Center 的只读 SQLite 连接获取：

```text
run_id / manifest / 股票池 / 排名 / 因子结果 / 上游权重
Investment Profile / Target Portfolio / Risk Snapshot / Exit Signal
```

每条证据都有 `evidence_id`、来源、数据时间和脱敏后的有界载荷。所有 Agent 只收到可序列化的 `CopilotEvidenceBundle`，不会收到数据库连接、服务对象、API Key 或交易方法。

Evidence Reader 对外提供四个语义化入口：`get_stock_evidence()`、`get_portfolio_evidence()`、`get_risk_evidence()` 和 `get_review_history()`。这些方法只封装同一个有界 `read()` 合同，不扩大数据范围，也不允许 Agent 自行查询数据库。

### Prompt Registry

当前注册表版本为 `copilot-prompts-v1.0.0`，五个 Prompt 分别标记 `research/portfolio/risk/review/coach-prompt-v1.0.0`。每次生成记录 `prompt_version`、`model_version`、Prompt哈希、`temperature=0` 和 `max_tokens=2200`。模型工具关闭，并强制返回如下 JSON：

```json
{
  "headline": "...",
  "summary": "...",
  "findings": [{"title": "...", "detail": "...", "evidence_ids": ["E-..."]}],
  "risks": [{"level": "warning", "message": "...", "evidence_ids": ["E-..."]}],
  "memory_candidates": [],
  "disclaimer": "仅解释已保存证据，不构成投资建议。"
}
```

### 发布前评估

`copilot-evaluation-v1.0.0` 在报告写入后、发布前检查：

1. 必填结构、字符串长度、风险等级和列表上限；
2. 每条发现、风险与记忆候选都必须引用证据；
3. 引用ID必须存在于本次 Evidence Bundle；
4. 输出数字必须能在本次证据中直接找到或为等值百分数；
5. 非 Coach Agent 禁止产生记忆候选。

任一检查失败，报告状态为 `rejected` 并返回隔离错误，不进入网页发布列表，也不会重试为订单或改变任何业务状态。通过后保存引用准确率、数字声明数、结构错误和人工1～5分评价。

### 显式任务审计

每次用户明确请求生成 Copilot 报告时，`CopilotService` 在证据验证后创建一条 `ai_tasks` 记录。任务只允许以下状态迁移：

```text
running → succeeded（generated / reused）
running → failed（rejected / error）
```

任务固定 `trigger=user_action`、`can_schedule=false`、`can_affect_execution=false`。模型失败只写一次失败终态，不自动重试；自动晨报、收盘任务和调度器不属于当前阶段。

### Copilot API

- `GET /api/v1/copilot/status`：版本、证据和模型就绪状态；
- `GET /api/v1/copilot/evidence`：只读证据预览；
- `POST /api/v1/copilot/reports`：生成盘前、收盘、个股、组合、风险或教练报告；
- `GET /api/v1/copilot/reports[/{report_id}]`：读取报告档案；
- `POST /api/v1/copilot/reports/{report_id}/rating`：人工质量评分；
- `GET/POST /api/v1/copilot/memory`：读取或保存非执行型记忆；
- `POST /api/v1/copilot/memory/{memory_id}/confirm`：确认 AI 候选记忆。

所有 Copilot 写操作继续受 Host、Origin 和 `X-Ashare-Client` 本机保护。响应固定包含 `can_trade=false` 或 `can_affect_execution=false`；系统中不存在 AI 到 Broker、OMS、ResearchPipeline、PortfolioService、Risk Engine 或 PaperPortfolio 的写入引用。
