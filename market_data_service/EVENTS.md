# 变更驱动的数据维护

页面不是采集器，也不负责定时唤起采集。采集继续由独立 Celery 调度和工作进程维护；业务仅通过 HTTP 读取独立数据库中的数据。

## 查询和通知

1. 数据、任务或策略写入时，数据库触发器在同一事务写入 `md_change_outbox`。回滚不会产生可见事件，任务租约续期也不会刷新页面。
2. 独立 `data-events` 进程先订阅 PostgreSQL `LISTEN`，再读取已提交的待发送记录。提交时的 `NOTIFY` 负责唤醒；持久化记录负责断线恢复。PostgreSQL advisory lock 保证每个数据库只有一个活动维护进程。
3. 维护进程合并一秒内的变化，只更新受影响的覆盖明细，并生成小体积的数据集汇总。汇总表和覆盖表不存储新闻正文或历史观察值载荷。
4. 汇总提交后使用 Redis Streams `XADD` 发送事件。成功后才确认待发送记录。崩溃或 Redis 不可用时允许重复投递、不会把未提交数据发出去；页面按最新完整状态覆盖，不累计重复增量。
5. 页面首屏读取快照，携带快照之前捕获的事件游标建立 SSE。后续用事件更新 TanStack Query 缓存；仅受影响且正在查看的明细会按需重新查询。无变化时没有概览、列表和已完成任务的定时 HTTP 请求。

事件流默认保留约 10,000 条（Redis 近似裁剪），不同数据服务数据库使用不同流。客户端断线后使用标准 `Last-Event-ID` 补发；游标超出窗口或 Redis 历史重建时发送 `reset`，重新获取快照。SSE 保活不是数据轮询，不访问业务数据表。

页面事件经业务 API 鉴权转发，数据服务凭证不进入浏览器。使用 FastAPI 原生 `EventSourceResponse`、浏览器原生 `EventSource`、Redis 官方客户端；浏览器再使用 `reconnecting-eventsource` 处理原生连接遇到 HTTP 5xx 后停止重连的边界，重试带随机抖动并携带 `lastEventId`。业务任务进度使用 `httpx-sse`，不自写 SSE 编解码或重连循环。游标优先级是 `Last-Event-ID` 请求头、`lastEventId` 查询参数、首屏 `after` 参数。

## 到期与故障

覆盖明细具有带索引的 `next_check_at`，按来源允许延迟、交易时段、报告期与订阅期限计算。独立维护进程在最近截止时间重新评估到期条目，而不是让每个页面扫描全市场。交易日历变化或失效会重新评估依赖它的数据。进程心跳和故障恢复最长间隔 30 秒，与在线页面数量无关。

待发送记录确认后保留七天用于故障排查；通知不可用不会删除未发送记录。数据服务仍可返回已保存的数据，但页面会区分推送连接与采集/事件维护进程健康，不能把旧快照显示为实时状态。

PostgreSQL 是实际运行和生产方案。SQLite 只用于本机开发/单元测试，不支持 LISTEN；其唯一维护进程集中检查待发送记录，不是每个页面/请求各自查库。生产配置已禁止 SQLite。

## 业务等待和上游采集

- 严格读取最多在**一次 HTTP 请求**内等待 30 秒。没有可用数据时，异步等待 Redis 事件，只有相关证券或请求发生变化才再读取；不再每隔 250 毫秒查库，也不再由业务客户端每秒重复提交。
- 上游目前使用已接入的 AKShare/HTTP API；不假装这些接口支持 WebSocket 推送。具备日期窗口的日线来源按重叠区间增量获取，保留完整的已维护窗口。
- 前复权数据合并前核对重叠区间价格、来源和日期。复权基准变化、来源切换、区间缺失或显式全量操作改走完整窗口；每七天进行全窗口校验，避免历史更正永久遗漏。
- 行情订阅在非交易时间将下一次常规刷新移到下一个交易时段。实时性仍由真实数据时间和来源能力决定，不由浏览器连接或页面刷新时间决定。

## 运行

`bash market_data_service/manage.sh start` 会执行迁移并更新 Supervisor 配置；`restart` 会保留数据库和任务记录。进程列表新增 `data-events`，日志位于 `.market-data/logs/events.log`。容器配置也包含独立 `events` 服务。

维护进程不可用时优先检查其日志、PostgreSQL 连接和 Redis；不要通过恢复页面高频刷新绕过故障。

官方接口参考：[FastAPI SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)、[PostgreSQL LISTEN](https://www.postgresql.org/docs/17/sql-listen.html)、[PostgreSQL NOTIFY](https://www.postgresql.org/docs/17/sql-notify.html)、[Redis XREAD](https://redis.io/docs/latest/commands/xread/)、[httpx-sse](https://github.com/florimondmanca/httpx-sse)。

连接恢复参考：[Fanout reconnecting-eventsource](https://github.com/fanout/reconnecting-eventsource)。浏览器离线事件会立即关闭连接并标识旧快照，联网后从已接收游标恢复；没有定时 HTTP 探测网络。
