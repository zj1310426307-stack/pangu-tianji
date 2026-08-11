# 盘古·天机 v0.2 架构与安全边界

## 处理链路

```text
合成日线数据
  → 趋势/动量信号
  → 目标仓位
  → 硬风控
  → MockBroker模拟执行
  → SQLite工作结果
  → 不可变运行快照
  → 网页只读展示
  → 可选模型研究解读
```

模型支路从“不可变运行快照”开始，到研究文本结束。它没有 Broker、RiskEngine、Strategy 或配置写入对象，也没有下单API。

## 独立状态

- `runtime`：idle / running / succeeded / failed；
- `safety`：paper模式、实盘锁定、kill switch；
- `model`：not_configured / checking / connected / error；
- `latest_backtest`：最近一次成功的不可变快照。

模型不可用不等于交易系统不安全；回测失败也不会自动打开安全停止。网页分别展示这些状态，避免把不同语义揉成一个“正常/异常”。

## 写入所有权

`RunService`只有一个后台工作线程。`ProjectRunLock`再用操作系统文件锁阻止第二个进程或命令行回测同时写入根级SQLite和报告。只有持有该锁的服务才能把中断的running记录恢复为failed。

成功后，`RunRepository`把工作结果复制到 `output/runs/<uuid>/`，原子写入 `run.json`，并记录配置与数据SHA-256。API每次请求以只读SQLite连接访问完成快照，不共享回测线程的连接。

## 本地网页边界

- Uvicorn只绑定 `127.0.0.1`；
- TrustedHost只接受127.0.0.1、localhost和测试主机；
- 所有写请求要求本地控制台请求头并校验Origin；
- CORS只允许当前本地控制台端口；
- API没有配置写入、实盘切换或订单提交端点；
- 前端用 `textContent` 展示模型文本，不注入HTML；
- 浏览器客户端由OpenAPI路径自动生成，并设置超时。

## 可复现性

一次成功运行包含：

- 运行编号、起止时间和阶段；
- 指标与独立报告；
- 净值CSV和SQLite活动明细；
- 当次 `settings.snapshot.yaml`；
- 配置SHA-256；
- 每个行情CSV的SHA-256。

模型解释旧回测时必须读取该次配置快照，而不能读取当前配置。
