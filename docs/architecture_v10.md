# 盘古·天机 v1.0 架构与金融状态边界

## 一致的策略链

```text
同花顺收盘快照 / 点时历史数据
  -> ResearchPipeline: Security Universe
  -> ResearchPipeline: Data Snapshot
  -> ResearchPipeline: Feature Calculation
  -> 保留的 factor_model.py: Factor Score
  -> ResearchPipeline: Ranking
  -> ResearchPipeline: Portfolio Construction
  -> 确定性风控
  -> 本地 PaperPortfolio
  -> 订单、成交、持仓、费用、净值与复盘
```

`DailyStockResearch` 的同花顺适配器与 `CrossSectionalBacktestEngine` 的历史数据适配器只负责读取和规范化点时数据。证券池过滤、技术特征、技术预筛、财务可见性、七维评分、排名、行业/相关性选择和波动率权重全部由同一个 `ResearchPipeline` 执行。策略版本为 `cross-sectional-v2.0.0`；保留的 `factor_model.py` 继续独立输出 `factor_model_version` 与 `factor_contract_hash`，防止策略流程和因子数学的版本语义混淆。

## Pangu V2 Data Center

```text
实时API / 历史点时表
  -> 首次采集Pipeline（确定动态输入范围）
  -> raw / clean / point_in_time / features / snapshots
  -> catalog.sqlite3
  -> DataCenterResearchDataSource读取持久化原始证据
  -> 同一个ResearchPipeline权威重放
  -> 实时计划或历史回测
```

生产实时研究与历史横截面回测都必须调用 `DataCenter.execute_pipeline()`。返回结果来自Data Center重放，不来自外部适配器的临时内存对象。排名、目标权重或拒绝原因无法确定性复现时失败关闭。

统一模型为Security Master、Market Data、Financial Point-In-Time、Feature Store和Research Snapshot，模型版本为 `pangu-data-model-v2.0.0`，存储Schema为 `2.0.0`。每个run的manifest包含数据来源、数据时间、策略版本、因子版本、因子合同哈希、数据版本、阶段计数、质量检查、最终排名和组合权重；历史回测排名额外携带 `run_id` 与 `data_version`。

表格资产按规范内容SHA-256寻址并GZIP保存；SQLite只承担轻量元数据目录。Data Center不读取账户、不参与下单、不调用模型，也不修改因子或风控。

## 盘中候选榜与正式计划

```text
手动盘中刷新 -> preview.json -> 网页候选榜（只读、不可执行）
15:10收盘研究 -> latest.json / history -> 次日有效模拟计划
```

盘中预览先删除当天尚未完成的日K，再把当前全市场快照作为临时观测点参与七维评分。预览固定返回 `mode=intraday_preview`、`used_for_execution=false`、`execution_ready=false` 和空 `targets`。模拟自动执行只读取 `latest.json`，从不读取 `preview.json`。

## 成交状态

```text
STOP_TRIGGERED
  -> EXIT_DEFERRED_T1
  -> EXIT_BLOCKED_LIMIT_DOWN
  -> EXIT_FILLED
```

模拟成交优先使用供应商涨跌停价；只在明确为主板、上市超过前五日时才从前收盘推导 10%/5% 价格限制。信息不足且接近涨跌停时失败关闭。

## 点时回测边界

- 股票池必须含上市和退市日期，不允许只用当前存续股票。
- 财务行必须带 `available_at`，且不得晚于信号日。
- 因子只用信号日及之前已完成数据，于下一交日开盘模拟成交。
- 回测执行 T+1、涨跌停锁定、停牌、整手、费用、滑点、止损、回撤停开仓和换手预算。
- 输出绝对收益、基准超额、信息比率、Beta/Alpha、Sortino、Calmar、回撤持续时间、月胜率、交易胜率、Profit Factor、换手和费用侵蚀。

## 券商式本地模拟柜台

网页行情来自同花顺轮询快照。手动买卖使用同一套服务端规则完成两阶段处理：

```text
参数校验
  -> POST /paper/broker/orders/preview（不落单）
  -> 展示费用、滑点、操作后现金/仓位和拒绝原因
  -> 用户显式确认
  -> POST /paper/broker/orders 重新报价与风控
  -> 即时模拟单直接成交，或DAY限价单进入PENDING
  -> 冻结现金/可卖股份
  -> 新鲜快照触价后本地全量撮合，或撤单/跨日过期
  -> 幂等写入订单、成交、账户与审计回执
```

卖出是 reduce-only，不会创建空头。买入必须是100股整手；卖出允许整手或一次性卖出整仓零股尾数，不允许任意零股部分卖出。活动买单参与现金、单股仓位、总仓位和持仓数量限制；活动卖单从可卖数量中扣除，止损监控不会重复卖出同一批股份。幂等键防止重试造成重复委托或成交。

当前数据源没有五档盘口、队列位置或券商成交回报，因此限价单只采用可解释的“未触价则等待、触价则整单模拟成交”模型，不伪造部分成交概率，也不声称复制交易所撮合。

## 持仓估值与复盘归因

唯一资产估值服务为 `ValuationService`（`valuation-v1.0.0`）。`PaperPortfolio` 只提供现金、持仓、成交、NAV和冻结资源等账本事实；`DailyResearchService` 与 `WorkbenchService` 共享同一个估值实例与行情短缓存。

```text
账本事实 + 持仓行情 + 冻结资源
  -> ValuationService
  -> asset_valuation / valued positions / equity_curve
  -> 首页 / 持仓 / 复盘 / 风险 / 模拟柜台
```

- `cash`、`market_value`、`equity`、`pnl` 和 `drawdown` 只有一套服务端公式；
- 持仓优先用同花顺轮询快照估值，并使用5秒短缓存避免并行面板重复请求。行情失败不会伪造零价格，而是显式回退账本估值/含费成本并展示来源与过期状态；
- 账户对账关系为：`PnL = 已实现盈亏 + 未实现盈亏 + 对账差额`。差额超过1分钱则由后端生成确定性风险告警；
- 当前净值点和整手购买力也由服务端生成，前端只格式化金额、百分比并绘制服务端点位；
- 复盘同时输出费用分解、换手、胜率、Profit Factor、平均盈亏、净值最佳/最差日、MFE/MAE、持有天数、止损距离和个股盈亏贡献；
- 为兼容旧接口保留的扁平账户字段仍由 `ValuationService` 派生，不构成第二估值源。

## 网页交互与状态所有权

网页按“今日 → 选股与模拟 → 持仓复盘 → 天机助手”组织，不在前端复制后端业务状态：

- 今日页把研究、计划、执行窗口和安全停止翻译为一个明确的下一步，并展示四阶段进度；主按钮只导航到对应操作，不自动提交订单。
- 候选榜默认使用可筛选卡片，可按代码、名称、理由或风险搜索，并在卡片与专业明细表之间切换。
- 被选候选的七维评分、推荐依据、风险标签和失效条件在独立详情区展示；选择股票不等于获取行情或提交订单。
- “带入模拟交易柜台”后才轮询该股票行情；买入与卖出仍要求用户确认，后端重新报价并拥有最终风控决定权。
- 长耗时研究、计划、执行和监控动作统一展示忙碌态、成功/失败提示，防止重复点击和结果不可见。
- 搜索、筛选、视图方式和当前候选只属于浏览器临时交互状态，不写入模拟账本，也不影响正式排名。

前端所有请求继续通过 `web/generated/client.js`；加入9个 Copilot 只读/受保护审计操作后，OpenAPI为39个操作。

## 独立实时调度与副作用边界

```text
页面可见
  ├─ 调度器每1秒判断到期任务（不发起固定全量请求）
  ├─ system通道：每10/15/30/60秒 GET 系统与模型状态
  ├─ daily通道：每10/15/30/60秒 GET 研究、计划与模拟账户
  ├─ review通道：每10/15/30/60秒 GET 持仓复盘
  ├─ quote通道：选股页每5秒 GET 所选股票同花顺轮询快照
  ├─ broker通道：交易时段且页面可见时每10秒 POST 活动委托快照撮合
  └─ candidates通道：交易时段每5/10/15分钟 POST 盘中候选预览重算

页面隐藏或用户暂停
  └─ 停止上述自动轮询；手动刷新仍可用
```

- 页面自动同步只读取后端权威状态，前端不复制持仓、订单、风控或计划业务规则。
- 每条通道拥有独立的 `inFlight`、`lastAttemptAt`、`lastSuccessAt`、`nextAt` 和失败次数；相同通道重复请求复用正在执行的Promise，不同通道可并行完成。
- 状态读取分别设置15～20秒超时；失败后采用指数退避，最大120秒，成功后立即恢复用户选定周期。一个慢接口或失败接口不会阻塞其他模块。
- 模块状态按各自周期计算新鲜度；成功数据超过阈值后标为“数据可能已过期”，而不是继续显示绿色成功。浏览器离线时保留最后快照，网络恢复后把到期时间置零并优先补采。
- 候选自动重算复用既有 `refresh_intraday_candidates` 操作，继续受本机请求头与Origin保护，并使用独立忙碌锁防止手动和自动请求重叠。
- 盘中重算只生成 `used_for_execution=false` 的预览；不会调用正式计划生成、模拟执行、持仓监控、买入或卖出端点。
- 页面切入后台时停止定时器，返回前台后立即进行一次轻量同步；行情轮询只在选股工作区可见且总开关启用时运行。
- 每个模块拥有独立的手动刷新入口和忙碌、成功、失败、过期或等待状态，整体同步不能掩盖单个数据链路失败。
- DeepSeek只同步连接状态。Copilot内容调用保持显式用户动作，避免自动产生费用，也保持 `can_trade=false`。
- 模拟订单预检、提交、撤单和撮合，以及 Copilot 报告/记忆/评价写入均受本机写请求防护；OpenAPI共39个操作，网页请求仍全部通过生成客户端。
- 页面关闭后，Windows持仓监控任务每5分钟也会处理活动委托；DAY限价单跨交易日自动过期并释放冻结资源。

上述设计参考了事件驱动量化系统的通道隔离、组件边界和任务生命周期，但没有照搬WebSocket或真实交易适配器：当前同花顺能力是HTTP轮询快照，因此保持可验证的轮询语义和本地模拟交易边界。

## Pangu V2 AI Investment Copilot

```text
Data Center ───────┐
                   ├─ Evidence Reader ─ Agent Task ─ DeepSeek JSON
Portfolio/Risk DB ─┘                         │
                                            v
                                  引用/数字/结构校验
                                     │           │
                                published     rejected
                                     │           │
                         ai_tasks终态 ─┴─ ai_copilot.db 审计
```

- `EvidenceReader` 是唯一证据入口；Data Center 使用公共 `manifest/read_asset`，Portfolio & Risk Center 使用 `PRAGMA query_only` 只读连接并做完整性检查。
- 五个 Agent 只负责塑造任务：Research解释排名和因子；Portfolio解释目标权重；Risk解释暴露和风险；Review解释收盘证据；Coach总结纪律并提出待确认记忆。
- Prompt Registry 固定版本、温度和输出上限；工具关闭、外部搜索关闭，不允许把市场常识或新闻写成当前系统事实。
- `CopilotEvaluation` 对JSON结构、证据引用和数字声明做确定性校验。失败报告保留审计记录但不发布。
- `ai_copilot.db` 独立保存 `prompt_registry`、`ai_reports`、`ai_memory`、`ai_evaluation` 和 `ai_tasks`，不复用研究、风险或交易表。
- `ai_tasks` 只记录用户显式生成动作；`trigger=user_action`、`can_schedule=0`、`can_affect_execution=0` 由数据库约束固化。报告生成、复用、校验拒绝和模型异常分别落为 `generated/reused/rejected/error`，本阶段无调度器、无后台重试。
- AI 记忆只允许 `profile/preference/decision/error_pattern/lesson`，AI推断默认是 `candidate`，必须人工确认；所有记忆 `can_affect_execution=0`。
- 相同 run、报告类型、标的、模型、Prompt和证据哈希直接复用已发布报告，避免重复成本。
- 网页“天机助手”展示六类报告、证据状态、报告历史、引用校验、人工评分和投资记忆；不在前端生成证据、评分或资产公式。

## 安全不变式

- `live_trading_enabled=false`；`can_submit_orders=false`。
- DeepSeek 只能解释 Evidence Reader 输出且通过发布校验的证据，`can_trade=false`、`used_for_execution=false`。
- 系统不包含证券账号、交易密码或券商报单方法。
- API Key 只存在当前 Windows 用户环境，不落盘至项目。
- 所有账本写操作只接受本机受信 Origin 与请求头。

## Pangu V2 Data Center

每次新生成的正式研究或盘中预览，都先由 `DataCenter` 保存完整研究证据，再发布计划。证据包含适配器边界原始输入、股票池与行情快照、特征、因子结果、排名及组合结果。运行目录位于 `output/data_center/runs/<日期>/<run_id>/`，采用 JSON+GZIP、SHA-256 清单和暂存目录原子发布。

`run_id` 格式为 `日期_策略版本_dv-数据版本_运行后缀`；相同输入保持相同 `data_version`，每次显式重跑仍获得唯一 `run_id`。Data Center 不拥有账户、订单、成交或风控状态，盘中预览仍不可执行，交易模块未改变。

## 已知数据缺口

同花顺当前代码表如未返回可验证的个股行业字段，生产日报会标记“行业中性化未生效”。历史生产回测只在用户提供完整点时 Parquet 数据后才显示 ready；实现回测引擎不等于已经证明策略有效。

## Pangu V2 Portfolio & Risk Center

```text
ResearchPipeline结果 + Data Center manifest + Investment Profile
  -> Security Risk（七类透明风险）
  -> PortfolioService（评分×风险×风格动态仓位）
  -> Target Portfolio（整手权重，execution_authorized=false）
  -> 正式计划使用目标权重进入既有本地模拟OMS
  -> PortfolioRiskEngine（压力回撤/行业/风格/规模/周期/集中度）
  -> ValuationService注入账户回撤
  -> ExitEngine（HOLD/WATCH/REDUCE/EXIT/DEFER_T1，creates_orders=false）
  -> Portfolio Risk Store（研究/账户风险证据）
```

Portfolio Service 只使用 ResearchPipeline 已批准目标和既有因子点数，先核对 Data Center 的策略版本、因子版本和合同哈希，再施加画像总仓位、单股上限、评分/风险/风格系数和整手约束。低风险预算不会被重新放大到满仓。Portfolio Risk Engine 不计算账户资产或实际回撤；`current_drawdown` 与 `max_drawdown` 必须来自 `valuation-v1.0.0`。

Investment Profile 同时以 `min(既有 paper_account 硬上限, 画像上限)` 组装模拟交易配置，仅收紧单股、总暴露和回撤阈值，不放松硬风控，不改变 PaperPortfolio 的状态机或公式。

`output/portfolio_risk_center.db` 独立保存 Investment Profile、Target Portfolio、目标/当前持仓、计划/账户风险和退出信号。研究结果幂等保存，模拟买卖/调仓/监控后保存账户风险，GET页面不产生写副作用。

这一层是组合决策和风险解释边界，不是交易状态机。正式模拟计划消费动态目标，但既有模拟订单硬风控、撮合和账户账本未被迁移；真实交易接口仍不存在。
