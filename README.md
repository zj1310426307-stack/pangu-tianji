# 盘古·天机 / Pangu V3.2

盘古·天机是本地运行的 A 股研究、数据治理与模拟交易系统。同花顺金融数据 API 提供沪深主板代码表、收盘快照、日线和财务指标；本地 MockBroker 负责跨日模拟账本、自动调仓和用户确认的模拟买卖。系统没有 SuperMind、QMT、PTrade 或真实证券下单接口。

## PANGU-V3.2-004 Workbench Valuation / NAV Semantics

“模拟盘实际持仓”现在以 `valuation_observed_at` 显示当前估值时间，不再把历史 `source_nav_date` 标成当前行情日期。Workbench `1.1.0` 同时返回当前估值观察时间、估值交易日、最近持久化净值日和 `nav_snapshot_state`，前端只展示服务端状态，不自行推导资产或日期语义。

Workbench 仅在行情快照新鲜、覆盖全部实际持仓、观察日等于工作台交易日且来源为可信同花顺持仓快照时，复用 `PaperPortfolio.mark_to_market()` 更新当日 `paper_nav`。陈旧、不完整、跨日或未信任快照只用于明确标记的只读估值，不会覆盖持久化净值；写入失败会保留已成功的当前估值并返回 `PERSISTENCE_FAILED` 告警。OpenAPI 与生成客户端仍为145个操作。

## PANGU-V3.2-003 Investment Journal、Review & Realtime Paper Account

投资复盘已形成“记录理由 → 到期复盘 → 用户确认 → 经验留存”的同页闭环。Journal 固定区分 `OBSERVE / DECISION / REVIEW / LESSON`，支持 `T+5 / T+20 / 自定义日期`；复盘草稿只整理已保存的研究、风险、退出和模拟账户事实，用户确认前不落经验。提醒仅为站内提醒，不能创建订单或调用模型。

模拟账户以 `PaperPortfolio / MockBroker` 为唯一持仓事实源。`max_positions=0` 表示不设置持仓只数上限，次日检查覆盖全部实际持仓，并合并当日正式研究目标；现金、单股15%、总仓位60%、整手、T+1、冻结股份、涨跌停、回撤和 Kill Switch 继续生效。买入当日可卖为0，下一交易日读取账户时自动释放为可卖；前端不自行推导数量。

桌面端现在由11条独立通道每15秒轮询系统、研究、账户、持仓复盘、投资运营、驾驶舱、个人OS、日志复盘、数据、策略进化、工程与可观测状态；账户成交/撤单/撮合后会立即失效驾驶舱缓存并同步所有账户消费模块。移动端前台每15秒同步当前页。这里的“实时”是同花顺HTTP轮询快照，不是逐笔行情或券商账户推送。

本阶段最终验证为 `332 passed, 1 warning`，OpenAPI 与生成客户端同步为145个操作；唯一warning是项目既有的Starlette TestClient/httpx弃用提示。详细审计见项目交付物《PANGU-V3.2-003 Investment Journal Review Reminder报告.md》。

## PANGU-V3.2-002 Personal AI Investment Assistant

桌面日常主导航已收敛为六项：`首页、AI助手、股票研究、我的持仓、投资复盘、设置`。专业AI报告、策略实验室、AI量化研究、数据中心、策略进化、工程健康、可观测、个人OS与运营中心仍完整保留，从设置页高级工具进入。

AI助手只支持今日关注、组合风险、个股关注理由、组合适配和行为复盘五类固定问题。唯一接口为 `POST /api/v1/assistant/query`；没有正式收盘run时不调用模型，模型文本出现交易指令时由服务端过滤。所有回答均附Evidence引用并固定 `can_trade=false`、`can_create_orders=false`、`can_launch_experiment=false`、`can_auto_remediate=false`。合同与使用边界见 `docs/personal_ai_investment_assistant.md`。

## PANGU-V3.2-001 Investment Dashboard

桌面首页现为“投资驾驶舱”：通过单一 `GET /api/v1/dashboard/overview` 在15秒快照内统一展示研究池市场状态、ValuationService资产、Portfolio Risk风险、证据型AI建议、全部已有评分候选、模拟持仓健康与今日任务。首页不再由浏览器拼接多接口，且不计算资产、PnL、仓位或回撤。

AI卡只读取与当前正式 `run_id` 匹配且含Evidence引用的已发布Copilot报告；没有Market Regime或历史持仓变化证据时明确显示不可用。刷新首页不会生成AI报告、修改策略/风控/组合或创建任何订单。架构和降级合同见 `docs/investment_dashboard.md`。

## PANGU-V3.1-001 Engineering Stabilization

V3.1 新增独立 `src/pangu/` 工程基础设施层，统一系统/策略/因子/Schema/AI 版本清单、分层配置、JSON 结构化日志、工程事件、四维健康检查和带 SHA-256 清单的本地备份。旧 `config/settings.yaml` 保持业务兼容；新配置分为 `base/strategy/risk/ai/scheduler/development/production.yaml`，两种环境都强制关闭实盘与真实券商。

网页新增 `09 工程健康`，只读展示版本、配置指纹、数据/数据库/AI/策略健康、备份与工程事件。显式“运行健康检查”和“创建本地备份”受既有本机写保护，且在 API、服务和数据库三层固定 `can_trade=false`、`can_create_orders=false`。完整架构见 `docs/engineering_stabilization.md`。

```powershell
.venv\Scripts\python.exe engineering_ops.py status
.venv\Scripts\python.exe engineering_ops.py health
.venv\Scripts\python.exe engineering_ops.py backup
.venv\Scripts\python.exe scripts\engineering_check.py
```

## PANGU-V3.1-002 Observability Platform

在工程健康快照之上新增连续运行证据：Telemetry Context、Metrics、Trace/Span、Job Run、SLO、告警、人工Incident和先计划后执行的Retention。网页新增“10 可观测中心”，展示由服务端聚合的p50/p95/p99、SLO证据、活动告警、Job时间线和Trace关联。

Observability不能修改策略、因子、组合、风控或订单，也不能自动修复/重跑任务。AI未启用为`NOT_APPLICABLE`，样本不足为`INSUFFICIENT_DATA`。详细Schema、指标字典、告警与SLO目录见 `docs/observability_platform.md`。

```powershell
.venv\Scripts\python.exe engineering_ops.py observability
.venv\Scripts\python.exe engineering_ops.py observe
.venv\Scripts\python.exe engineering_ops.py alerts
```

## PANGU‑V3‑001 Data Intelligence Platform

Pangu V3 首阶段新增 Data Intelligence Platform：对每个不可变 Data Center run 执行质量、时效、覆盖、异常和一致性五类确定性检查，以固定权重 `25/20/20/20/15` 生成数据健康分，并保存数据目录、血缘和事件证据。正式收盘研究只有在数据状态为 `NORMAL` 时才能发布；`WARNING`、`ERROR`、`BLOCKED` 或缺少评估都会失败关闭。人工“确认事件”只表示已查看，不能解除门禁，也不能修改历史数据、因子、策略或交易。

网页新增 `07 数据中心`，可查看健康分拆解、事件、目录、血缘和安全边界。新增 7 个 `/api/v1/data-intelligence/*` 操作，OpenAPI 共 103 个 operation；全量结果为 `270 passed, 1 warning`，22 个受保护核心文件哈希全部一致。详细设计见项目交付物《PANGU-V3-001 Data Intelligence Platform报告.md》。

## PANGU‑V2‑010 Personal Investment OS

网页现在默认进入 `00总控`：统一展示 ValuationService 资产快照、个人投资过程评分、AI 教练、投资者数字孪生、模拟成交事件链、投资日志、个人知识库、每周投资委员会和月度复盘。BUY/SELL 事件只能从既有 MockBroker 成交同步；用户只能手工记录 OBSERVE、REVIEW 和 LEARN。个人画像、日志、知识和报告均不能修改生产策略、因子、组合、风控或订单。

过程评分固定为纪律25、风险25、研究20、记录15、复盘15，只评价过程，不评价收益；缺失证据会降低覆盖率，不会重新分权或编造结论。新增 18 个 `/api/v1/personal-os/*` 操作，OpenAPI 共 96 个 operation；全量结果为 `260 passed, 1 warning`。详细设计、使用流程与安全边界见 `06-交付物/PANGU-V2-010 Personal Investment OS报告.md`。

## v1.0 的金融专业修正

- 实时研究和横截面回测共用同一个 `ResearchPipeline`：证券池、数据快照、特征计算、七维因子评分、排名和组合构建均执行 `cross-sectional-v2.0.0`；保留的 `factor_model.py` 继续独立标识七维因子合同。
- 因子支持 1%/99% 去极值、市值中性化和行业内排名；数据源不提供行业归属时会显式告警，不伪造中性化结果。
- 历史回测必须使用动态历史股票池和带 `available_at` 的点时财务数据，信号于次日开盘执行；旧 ETF 回测不得冒充生产策略验证。
- 成本默认建模为佣金 0.03%、最低 5 元、卖出印花税 0.05%、过户费 0.001% 和滑点 0.1%；实际费率应以交割单校准。
- 买入均价包含买入费用；复盘显示已实现/未实现/净盈亏、费用分解、换手、MFE/MAE、止损距离和个股归因。
- 涨跌停、停牌和报价缺失是独立成交状态。止损触发不等于必然成交，跌停时记录 `EXIT_BLOCKED_LIMIT_DOWN`。
- 组合默认最多 5 只、单只不超过 15%、总仓位不超过 60%；前 8 名进入、前 20 名继续持有、最短持有 5 天、单日换手预算 25%，并检查行业集中和相关性。
- 网页模拟买卖采用券商式本地柜台：服务端预检后可提交“即时模拟”或“当日限价”委托；限价单冻结资金/可卖股份，达到快照价格后成交，支持撤单与跨日过期。整仓零股尾数可一次性卖出，任意零股部分卖出仍拒绝。

## 默认工作流

1. 交易日内可点击“盘中刷新候选榜”，按同花顺最新全市场快照生成只读预览；预览不覆盖正式计划，也不触发模拟成交。
2. 交易日 15:10 之后收集完整收盘数据，输出正式候选榜和次日计划。
3. 次日 09:35–09:45 只执行前一交易日的有效正式计划；漏跑不补单。
4. 09:35–14:55 每 5 分钟监控持仓。8% 止损遵守 T+1 和跌停无法成交约束。
5. 网页可手动提交即时模拟或限价买卖；限价单进入当前委托，等待轮询快照达到价格后成交，也可手动撤单。所有结果只写入本地账本。

## 交互式工作台

- 首页根据当前研究、正式计划、执行窗口和Kill Switch状态给出唯一的“下一步”，避免在多个按钮之间猜测。
- 候选榜默认显示评分卡片，支持搜索、风险/目标筛选，以及卡片和专业明细表切换。
- 点击候选卡片可查看七维评分、推荐依据、风险标签和失效条件；再点击“带入模拟交易票据”才会读取该股票行情。
- 模拟柜台展示总资产、可用/冻结资金和股票市值，支持100/300/500股快捷数量、即时模拟与DAY限价。提交前展示预计成交价、费用、滑点、操作后现金和总仓位；活动限价单可在“当前委托”撤销。
- 持仓复盘优先用同花顺快照重新估值，失败时明确回退最近净值或含费成本；页面分开展示已实现、未实现、净盈亏、胜率、盈利因子、对账差额和个股贡献。
- 首页、持仓、复盘、风险和模拟柜台共用 `ValuationService`（`valuation-v1.0.0`）：现金、市值、权益、PnL、回撤、持仓权重、当前净值点和整手购买力全部由服务端生成，网页只负责格式化与绘图。
- “天机助手”已升级为 `ai-investment-copilot-v1.1.0`：Evidence Reader 只读取 Data Center 与 Portfolio & Risk Center 的版本化证据，Research、Portfolio、Risk、Review 和 Coach 五类 Agent 可生成盘前、收盘、个股、组合、风险与纪律报告。
- 每份 Copilot 报告保存 `run_id`、模型版本、Prompt版本、证据ID、证据哈希和自动评估；引用不存在、数字无法在证据中找到或输出结构错误时拒绝发布。每次显式生成还写入不可调度、不可影响交易的 `ai_tasks` 审计记录；投资记忆须由用户明确保存或确认，且 `can_affect_execution=false`。
- 全市场研究、正式计划、模拟执行和持仓监控都有加载状态与完成提示；手机端采用单列布局且无页面横向溢出。
- 在选股页按 `/` 可直接聚焦候选搜索，按 `Esc` 清空搜索。

## 独立实时更新引擎

- 页面默认每15秒同步系统、研究/账户、持仓复盘、投资运营、驾驶舱、个人OS、日志复盘、数据智能、策略进化、工程健康和可观测状态，可在10、15、30或60秒之间调整。11条状态通道互不阻塞，不再由一个总请求串行等待。
- 进入“选股与模拟”后，所选股票按配置的5秒周期读取同花顺最新快照；离开该页、切到浏览器后台或暂停实时同步后停止轮询。
- 选股页可见且处于交易时段时，活动限价单每10秒执行一次显式快照撮合；Windows监控任务每5分钟也处理活动委托，页面关闭后仍可继续低频模拟撮合。
- 全市场候选榜可在交易时段每5、10或15分钟自动重算。自动重算只写入 `preview.json`，不覆盖收盘正式计划、不执行模拟买卖。
- 实时更新中心分别显示各业务模块、所选行情和候选重算的最新状态；每个模块可单独手动更新，单个模块失败不会伪装成整体成功，也不会延迟其他模块。
- 每条通道都有独立的进行中锁、超时、最近成功时间和指数退避重试；成功后恢复正常周期，失败时最长退避到120秒。超过各自新鲜度阈值后，页面会把旧数据标为“数据可能已过期”。
- 浏览器离线时保留最后一次成功快照并暂停外部行情/候选请求；网络恢复后优先补一次到期通道。重复点击只复用当前请求，不会叠加同一模块的并发调用。
- DeepSeek只自动同步连接状态，不自动调用收费模型；所有 Copilot 报告仍需用户明确点击生成。
- 同花顺数据仍是轮询快照，不是逐笔行情、分钟K线或五档盘口。页面刷新也不代表成交。

## 启动

先将同花顺 Key 保存为当前 Windows 用户环境变量：

```powershell
setx THS_FINANCE_API_KEY "你的Key"
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
启动网页控制台.cmd
```

打开 `http://127.0.0.1:8765`。DeepSeek Key 可直接在“天机助手”页保存；模型只解释经过 Evidence Reader 裁剪且通过校验的确定性证据，不参与评分、风控或订单。两类 Key 均不写入项目、日志、数据库或 OpenAPI。

## 生产同构历史回测

实时同花顺适配器和历史 Parquet 适配器只负责提供点时数据，不再各自实现特征、评分和选股。两者都调用 `src/ashare_agent/research_pipeline.py`，计划和回测指标同时输出 `strategy_version=cross-sectional-v2.0.0`、`factor_model_version` 与 `factor_contract_hash`。

将以下不可变 Parquet 数据放入 `data/cross_sectional/`：

- `bars.parquet`：`date,symbol,open,high,low,close,volume,turnover`，可附加涨跌停价。
- `fundamentals.parquet`：至少含 `symbol,available_at,profit_growth,revenue_growth,roe,cash_quality`；`available_at` 必须是当时真正可见日期。
- `universe.parquet`：`symbol,list_date,delist_date,name,industry`，必须包含历史退市标的。
- 可选 `benchmark.parquet`：`date,close`。

```powershell
.venv\Scripts\python.exe scripts\run_cross_sectional_backtest.py
```

结果位于 `output/cross_sectional_backtest/`，包含排名、成交、净值和绝对/相对绩效。缺少上述三类点时数据时，网页会明确显示“生产策略未完成历史验证”，旧 ETF 回测仅作兼容功能。

## 主要 API

- `GET /api/v1/market/quote`：同花顺轮询快照及模拟买/卖可用性。
- `POST /api/v1/paper/orders/preview`：只读计算最新报价、费用、滑点、操作后账户和确切拒绝原因，不创建订单。
- `POST /api/v1/paper/orders/buy`：本地模拟买入。
- `POST /api/v1/paper/orders/sell`：本地减仓卖出。
- `POST /api/v1/paper/broker/orders/preview`：预检即时或限价模拟委托。
- `POST /api/v1/paper/broker/orders`：创建持久化本地模拟委托。
- `POST /api/v1/paper/broker/orders/{client_order_id}/cancel`：撤销活动模拟委托并释放冻结。
- `POST /api/v1/paper/broker/match`：使用一批新鲜同花顺快照执行一次本地撮合。
- `POST /api/v1/daily-research/preview`：手动刷新当前交易日只读候选榜。
- `POST /api/v1/daily-research/collect`：收盘研究。
- `POST /api/v1/daily-research/execute`：执行有效模拟计划。
- `POST /api/v1/daily-research/monitor`：运行止损监控。
- `GET /api/v1/workbench`：读取实际模拟持仓和完整审计证据。
- `GET /api/v1/copilot/status`：读取 Agent、证据、模型、报告和记忆就绪状态。
- `GET /api/v1/copilot/evidence`：预览某类报告将读取的脱敏证据包。
- `POST /api/v1/copilot/reports`：显式生成一份证据化报告；失败校验的报告拒绝发布。
- `GET /api/v1/copilot/reports` 与 `GET /api/v1/copilot/reports/{report_id}`：读取报告审计档案。
- `POST /api/v1/copilot/reports/{report_id}/rating`：保存1～5分人工评价。
- `GET/POST /api/v1/copilot/memory`：读取或保存非执行型投资记忆。
- `POST /api/v1/copilot/memory/{memory_id}/confirm`：显式确认 AI 提出的候选记忆。
- `GET /api/v1/data-intelligence`：读取数据健康、事件、目录和血缘总览。
- `POST /api/v1/data-intelligence/evaluate`：显式评估一个已存在的不可变 Data Center run；不采集数据、不修改历史记录。
- `GET /api/v1/data-intelligence/health` 与 `GET /api/v1/data-intelligence/incidents`：读取健康历史和数据事件。
- `POST /api/v1/data-intelligence/incidents/{incident_id}/acknowledge`：标记事件已查看；不能解除研究发布门禁。
- `GET /api/v1/data-intelligence/catalog` 与 `GET /api/v1/data-intelligence/lineage/{run_id}`：读取数据资产目录和研究血缘。
- `GET /api/v1/engineering`：读取版本、配置指纹、最近健康、备份与结构化事件。
- `POST /api/v1/engineering/health/run`：显式执行四维只读工程健康检查。
- `POST /api/v1/engineering/backups`：创建本地、非覆盖、带 SHA-256 清单的工程证据备份。
- `GET /docs`：完整 OpenAPI。

桌面写操作继续受本机请求头与 Origin 保护；移动端只开放独立的 JWT 受限接口，不能调用任何桌面交易或配置写操作。两个网页都只使用由 OpenAPI 生成的 `web/generated/client.js`。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest -q tests
.venv\Scripts\python.exe scripts\sync_openapi.py
node --check web\app.js
node --check web\mobile\mobile.js
node --check web\generated\client.js
```

PANGU‑V3.1‑001 当前全量结果为 `291 passed, 1 warning`；唯一 warning 是既有 Starlette/httpx 测试组件弃用提示。OpenAPI 已同步为 121 个操作，28 个受保护核心文件哈希差异为 0。工程健康实机检查为 `DEGRADED 84/100`：11 个 SQLite 数据库均通过 `quick_check`，但 Data Center 尚无正式 latest 研究快照，DeepSeek 已配置但本次只读状态未做联网连接测试。

详细状态边界见 `docs/architecture_v10.md`。本软件仅供研究和工程验证，不承诺盈利，不构成投资建议。

## Data Center

正式收盘研究、盘中只读预览和生产横截面回测统一通过 `DataCenter.execute_pipeline()`：先把本次输入保存到 `output/data_center/`，再由 `DataCenterResearchDataSource` 读取持久化证据并权威重放同一个 Research Pipeline。重放排名或权重不一致时失败关闭。

物理目录分为 `raw/`、`clean/`、`point_in_time/`、`features/` 和 `snapshots/`；`catalog.sqlite3` 只索引运行、数据资产与五类模型。每个运行都有同时包含研究日期、`strategy_version` 和 `data_version` 的唯一 `run_id`，manifest保存数据来源、数据时间、因子版本/合同哈希、最终排名和组合权重。

Data Center 只负责研究数据血缘，不读取模拟账户、不生成订单，也不改变交易或风控逻辑。任何疑似 Key、Token 或密码字段都会拒绝落盘。详细设计见项目交付物《Pangu Data Center设计报告.md》。

## 统一账户估值

模拟账本、持仓行情和冻结资源统一进入 `src/ashare_agent/services/valuation_service.py`。`GET /api/v1/daily-research` 与 `GET /api/v1/workbench` 都返回同一 `asset_valuation` 合同，核心字段为 `cash`、`market_value`、`equity`、`pnl` 和 `drawdown`。新增页面不得在路由或 JavaScript 中复制这些公式。

## Portfolio & Risk Center

`InvestmentProfile`、`PortfolioService`、`PortfolioRiskEngine` 和 `ExitEngine` 将 Data Center 的可追溯研究证据转换为用户画像约束下的目标组合、风险视图、调仓差异和退出意图。默认画像为 10 万元、平衡风险、中期、最大回撤容忍 10%、均衡风格，可在 `config/settings.yaml` 的 `investment_profile` 中修改；也支持 `medium`、`3_year`、`max_drawdown=15` 等常用输入并规范为内部合同。

动态仓位使用“基础权重×评分系数×风险系数×风格系数”，再施加画像总/单股上限并按100股整手反算。正式研究计划的本地模拟目标权重采用这一结果，但订单仍须经过既有模拟OMS和硬风控；Portfolio Service与Exit Engine不直接创建订单。

Investment Profile 会以 `min(原模拟盘限额, 画像限额)` 收紧单股仓位、总暴露和回撤停止开仓阈值；只允许变得更保守，不会因激进画像放松既有 `paper_account` 硬风控。

## AI Investment Copilot

`src/ashare_agent/ai_copilot/` 提供 `EvidenceReader`、五类 Agent、版本化 Prompt Registry、报告审计、投资记忆和输出评估。生成链为：

```text
Data Center + Portfolio & Risk Center
  → Evidence Reader（run_id / evidence_id / 脱敏 / 有界）
  → 专职 Agent 任务合同
  → DeepSeek JSON 完成
  → schema / 引用 / 数字校验
  → published 或 rejected
  → 本地 ai_copilot.db 审计
```

AI 报告数据库与研究数据、组合风险数据库和模拟交易账本隔离。`ai_reports`、`ai_memory`、`ai_evaluation` 和 `ai_tasks` 均处于非执行边界；任务只由用户显式生成报告时创建，成功、复用、拒绝和异常都有终态，不自动重试、不定时运行。模型调用关闭工具，串行限流，温度固定为0，不能接触数据库句柄、数据密钥或任何 Broker/OMS 方法。收盘复盘缺少账户收益证据时必须说明证据不足，不允许根据价格变化自行补算。

个股风险覆盖波动、回撤、技术、基本面、估值、流动性和可选资金流；组合风险增加压力回撤、行业、因子风格、规模、周期和集中度。账户实际回撤只读取 `asset_valuation`。研究与账户风险证据保存在独立 `output/portfolio_risk_center.db`，普通页面读取不写数据库。完整规则见项目交付物《Portfolio Risk Center设计报告.md》。

## Daily Investment OS

`src/ashare_agent/services/daily_investment_os_service.py` 将研究、组合风险、统一估值和 AI Copilot 组织为只读的每日投资运营层。四类任务为盘前晨报、盘中监控、盘后复盘和周报；报告、证据引用、任务终态和通知保存在 `output/daily_investment_os.db`。

调度器只能调用 `generate_report()`，不会调用模拟撮合、止损、调仓或下单方法。模型失败时确定性报告以 `degraded` 发布；核心证据缺失时失败关闭。`can_trade` 与 `can_create_orders` 在合同、服务和 SQLite 三层固定为 `false`。

手工运行示例：

```powershell
.venv\Scripts\python.exe daily_os.py run morning_report --force
.venv\Scripts\python.exe daily_os.py run intraday_monitor --force
.venv\Scripts\python.exe daily_os.py run closing_review --force
.venv\Scripts\python.exe daily_os.py run weekly_report --force
```

Windows 自动调度可使用 `安装投资运营任务.cmd`，但交付时不会自动安装。激活前请先核对旧版“每5分钟模拟交易 monitor”任务，两者职责不同。详细合同见 `docs/daily_investment_os.md`。

新增的本地 API：

- `GET /api/v1/investment-os`
- `GET /api/v1/investment-os/reports`
- `GET /api/v1/investment-os/reports/{report_id}`
- `GET /api/v1/investment-os/notifications`
- `POST /api/v1/investment-os/jobs/{job_name}/run`
- `POST /api/v1/investment-os/notifications/{notification_id}/read`

## Mobile Investment Assistant

PANGU-V2-007 新增移动 Web 投资助手，入口为 `/mobile/`。默认桌面启动仍只监听 `127.0.0.1`；只有双击 `启动移动助手.cmd` 或显式运行下列命令时才开放可信局域网访问：

```powershell
.venv\Scripts\python.exe server.py --mobile --port 8765
```

电脑本机打开 `http://127.0.0.1:8765/mobile/` 生成 8 位、5 分钟、单次使用的配对码；手机连接同一可信局域网后打开 `http://<电脑局域网IP>:8765/mobile/` 完成配对。JWT 仅存于浏览器当前会话；未设置 `PANGU_MOBILE_JWT_SECRET` 时使用运行期随机密钥，服务重启后需重新配对。

移动页面包括投资驾驶舱、组合与风险、股票证据、天机助手、四类运营报告、通知中心和投资日志。所有资产字段原样来自 `ValuationService`，候选与股票详情只接受 `formal_close_plan` 正式证据，盘中 preview 不会被提升为正式结论。

移动端的权限上限固定为：不能创建模拟或实盘订单，不能修改策略、组合、风控和模型配置，也不能读取券商凭据。远程局域网请求只能访问 `/mobile/` 和 `/api/mobile/v1/*`；即使伪造本机请求头或移动 JWT，也无法访问旧 `/api/v1` 交易接口。

当前 LAN 入口为 HTTP，只能用于可信家庭或办公网络，禁止端口映射或公网暴露。正式微信小程序与外网访问仍需 HTTPS 网关、设备撤销、持久化限流和另行安全评审。完整合同见 `docs/mobile_investment_assistant.md`。
