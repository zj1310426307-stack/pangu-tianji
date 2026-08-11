# Daily Investment OS 运行与接口说明

## 运行方式

```powershell
python daily_os.py status
python daily_os.py due
python daily_os.py run morning_report --force
```

Windows 可以手动运行：

- `安装投资运营任务.cmd`
- `运行投资运营任务.cmd`
- `卸载投资运营任务.cmd`

安装脚本不保存任何 API Key。本次交付没有自动安装 Windows 任务。

## 时间表

| 任务 | 默认时间 | 输出 |
|---|---|---|
| `morning_report` | 交易日 08:45 | 盘古晨报 |
| `intraday_monitor` | 09:45–14:45，30/60分钟 | 盘中风险监控 |
| `closing_review` | 交易日 15:30 | 盘后投资复盘 |
| `weekly_report` | 周五 16:00 | 盘古周报 |

任务使用 Asia/Shanghai。命令行会优先复用同花顺交易日历；无法取得时默认至少跳过周末。盘中任务排除午休。

## 本机 API

- `GET /api/v1/investment-os`
- `GET /api/v1/investment-os/reports`
- `GET /api/v1/investment-os/reports/{report_id}`
- `GET /api/v1/investment-os/notifications`
- `POST /api/v1/investment-os/jobs/{job_name}/run`
- `POST /api/v1/investment-os/notifications/{notification_id}/read`

POST 需要本机 `X-Ashare-Client: local-dashboard` 及受信 Origin。所有 Daily OS 响应均包含 `can_trade=false` 和 `can_create_orders=false`。

## 邮件（可选）

`config/settings.yaml` 中默认 `email_enabled: false`。开启前使用当前 Windows 用户环境变量配置：

```text
PANGU_SMTP_HOST
PANGU_SMTP_PORT
PANGU_SMTP_USERNAME
PANGU_SMTP_PASSWORD
PANGU_SMTP_FROM
PANGU_SMTP_TO
```

系统不回显也不落盘保存这些值。

