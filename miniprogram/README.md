# 盘古·天机微信小程序边界说明

`miniprogram/` 是 PANGU-V2-007 预留的小程序工程边界，不是已可发布的微信小程序。当前交付以 `/mobile/` 移动 Web 为主，小程序不复制 Python 研究、风控、AI 或账户逻辑。

## 预定页面映射

| 小程序页面 | 移动 Web | 后端能力 |
|---|---|---|
| `pages/dashboard/` | `#dashboard` | 驾驶舱、ValuationService估值、市场、风险与AI摘要 |
| `pages/portfolio/` | `#portfolio` | 持仓、服务端盈亏、风险与退出证据 |
| `pages/stock/` | `#stock` | 点时股票评分、因子、风险和AI解释 |
| `pages/ai/` | `#assistant` | 证据型 Copilot 问答与审计历史 |
| `pages/report/` | `#reports` | 晨报、盘中、收盘和周报证据 |
| `pages/journal/` | `#journal` | 非执行性投资日志 |
| `pages/notifications/` | 顶栏通知抽屉 | INFO / WARNING / CRITICAL 通知 |

## 认证与角色

1. 用户仅能在电脑本机页面生成短时、一次性配对码。
2. 手机使用配对码换取有限时 JWT，后续通过 `Authorization: Bearer` 访问移动 API。
3. 角色只显示为“个人主人”或“只读访客”。个人主人仅比访客多日志、AI问答和通知已读权限，不代表可修改策略、风控或组合，也不能创建任何订单（含模拟/实盘）。
4. JWT 不写入项目、配置、日志或长期本地存储。移动 Web 只保存在 `sessionStorage`；小程序正式开发前需单独安全评审令牌存储方案。

## API 合同

小程序必须与移动 Web 共用 FastAPI 的 `/api/mobile/v1/*` DTO，不允许手写另一套后端请求或字段映射。开发前应基于 `docs/openapi.json` 生成小程序客户端，并至少覆盖：

- `get_mobile_dashboard`
- `get_mobile_portfolio`
- `get_mobile_stock_detail`
- `create_mobile_copilot_chat`
- `list_mobile_reports` / `get_mobile_report`
- `list_mobile_notifications` / `mark_mobile_notification_read`
- `list_mobile_journal` / `create_mobile_journal_entry`
- `get_mobile_auth_status` / `pair_mobile_device` / `get_mobile_session`

资产、收益、仓位、回撤与风险必须直接展示服务端 DTO，页面不重新计算。

## 发布前必做

- 注册微信小程序与合法 HTTPS 域名；
- 完成公网网关、TLS、限流、令牌撤销、审计与隐私合规评审；
- 生成并锁定小程序 OpenAPI 客户端；
- 在真机验证配对、令牌过期、敏感信息零落盘和所有 `can_trade=false` 边界；
- 另行获得用户的公网发布授权。

当前未进行微信平台注册、真机调试、公网部署或发布。
