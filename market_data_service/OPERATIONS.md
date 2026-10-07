# 独立数据服务：部署与运维

## 职责与边界

数据服务拥有行情数据、证券主数据、交易日历、财务数据、资讯、来源订阅、同步策略、任务进度和不可变数据版本。业务拥有用户、自选股、分析任务与 LangGraph/LangChain 运行状态。两者不共享数据库、不跨库查询。

业务唯一入口是 `src/services/market_data_client.py`，通过 HTTPX 连接池调用 `/v1` API。页面通过业务侧 `/api/v1/data-service` 转发，因此浏览器看不到服务凭证。服务断开或数据未达标时明确返回不可用/待更新，不回退到旧业务库或直接抓取上游。

实现复用 FastAPI/Pydantic、SQLAlchemy、Alembic、Celery/Redis、Supervisor，以及项目已有的 AKShare/RSS 数据适配器。分析公式、筛选、证据判断仍在业务层；采集任务不调用大模型。

页面使用 SSE 变更推送；查询汇总由独立事件进程维护，明细由数据库分页。事务待发送记录、断线补发、到期更新、增量采集与运行说明见 [EVENTS.md](EVENTS.md)。

标准机制依据：[Celery 周期任务](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html)、[Redis 消息可见性超时](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)、[SQLAlchemy PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert)、[Python contextvars](https://docs.python.org/3/library/contextvars.html)。

## 自动维护与“最新”

| 数据集 | 默认检查间隔 / 最大检查年龄 | 范围及判断依据 |
| --- | --- | --- |
| 证券主数据 | 1 天 / 2 天 | 全 A 股；阻止不完整名单导致批量错误退市 |
| 交易日历 | 1 天 / 7 天 | 上游交易日历；缺失时不使用工作日猜测 |
| 日 K 线 | 1 小时 / 1 天 | 全 A 股；最近完整交易日，默认维护 500 根前复权日线 |
| 财务报表 | 6 小时 / 12 小时 | 全 A 股；报告期、同口径 TTM、必需字段完整性 |
| 实时行情 | 1 分钟 / 2 分钟 | 近期订阅；校验上游真实报价时间与交易时段 |
| 公司新闻 | 15 分钟 / 30 分钟 | 近期订阅；成功核查时间、来源结果与原始链接 |
| 公司公告 | 30 分钟 / 1 小时 | 近期订阅；公告与研究报告来源 |
| 市场与资金流 | 5 分钟 / 15 分钟 | 近期使用的行业、市场、资金流和估值查询 |
| 指数与宏观 | 1 小时 / 1 天 | 近期订阅；保留来源发布期/时间 |
| 资讯订阅 | 15 分钟 / 30 分钟 | 最近使用的 RSSHub 路由、正文及参数目录 |

“近期订阅”是最近 7 天使用过的请求，首次读取后自动建立并持续维护；不是预先抓取互联网上的所有新闻或任意路由。首次无数据时返回 202 并提交后台补采，客户端有界等待，超过等待预算返回明确的待更新错误。关闭某类自动维护不会删除数据，主动读取仍可补采。

“最新”表示符合该数据集的时效与完整性约定，不能保证公共上游实时、不断线或覆盖所有证券：

- 日线按上海时区和交易日历判断，15:15 前只要求前一完整交易日；周末/休市不误报。股票停牌或上游缺当天数据时会明确显示未达标，不伪造日线。
- 成交量统一为股，成交额为元。腾讯日线的 `amount` 实为手数，不冒充成交额；缺少成交额时保持缺失。
- 财务快照按报告期整体替换，不跨报告期填补空值；TTM 与资产负债率缺失时标为不完整。来源未覆盖应披露期时标为过期。
- 行情必须有真实报价时间；没有时间、未来异常时间或过时行情都不能仅凭抓取成功变成“最新”。
- 资讯、目录等没有统一行情时间的数据，以成功检查年龄为依据，并保留原始发布时间。检查时间不是新闻发生时间。
- 每股/每订阅分别统计 `fresh / stale / missing / partial / unknown / failed`，不能用一只股票的最新日期代表全市场。
- 历史区间和短窗口不覆盖当前完整日线版本。显式 `allow_stale` 可以读取旧值，但响应保留状态；默认严格读取不把旧值交给业务当作最新数据。
- 采集刷新绕过内部缓存；并行子请求使用原生 `copy_context()` 传递刷新上下文，避免缓存内容被重新认证为新数据。

## 推荐部署：Docker Compose

需要可用的 Docker daemon。以下命令均从仓库根目录执行。

1. 从 `.env.market-data.example` 创建 `.env.market-data`。设置不同用途的随机数据库密码和至少 32 字符 API token，不提交到 Git。数据库密码使用 URL 安全字符。
2. 启动独立服务：

```bash
docker compose --env-file .env.market-data -f market_data_service/compose.yaml config -q
docker compose --env-file .env.market-data -f market_data_service/compose.yaml up -d --build
docker compose --env-file .env.market-data -f market_data_service/compose.yaml ps
```

Compose 依次启动 PostgreSQL 17、Redis 7.4、一次性 Alembic 迁移、API、来源采集进程、全市场同步进程、事件维护进程和一个 Celery beat。PostgreSQL/Redis 不发布主机端口；API 仅绑定 `127.0.0.1:8010`。数据库与 Redis AOF 使用独立持久卷。

业务配置：

```dotenv
MARKET_DATA_SERVICE_URL=http://127.0.0.1:8010
MARKET_DATA_SERVICE_TOKEN=与数据服务API_TOKEN相同的值
```

业务运行在其他容器/主机时，将 URL 配为其可访问的私网地址；不要让浏览器直连数据服务。跨主机采用私网、防火墙和 TLS。服务生产模式强制 PostgreSQL、至少 32 字符 token 和 live provider，禁止测试夹具。公开部署还需沿用业务系统的认证及限流。

RSSHub 是独立上游，地址由 `MARKET_DATA_RSSHUB_URL` 指定。容器默认通过 `host.docker.internal:1200` 访问主机 RSSHub；也可连接已有独立 RSSHub。Compose 不启动大模型、业务或 RSSHub。

## 本机开发与现有项目

开发服务使用自己的 Python 虚拟环境，不需要安装 LangChain/LangGraph：

```bash
bash market_data_service/manage.sh install
bash market_data_service/manage.sh start
bash market_data_service/manage.sh status
```

首次需要先准备独立 PostgreSQL 数据库与 Redis，并在 `.env.market-data` 填写连接串；脚本不会初始化系统默认数据库。示例使用 PostgreSQL 5433、Redis 6381 和 `market_data` 数据库，这些不是新克隆仓库自带的服务。可以自行部署，或使用上面的 Docker Compose。脚本只会恢复该项目明确初始化的 `.market-data/postgres` 集群及 Redis；未初始化时须先准备基础设施。不要复用业务数据库或其他服务的 Redis。

开发 API 为 8010；源码与状态日志在 `.market-data/logs`。`restart` 加载新代码；`stop` 只关闭 API/采集/调度，不删除或停止 PostgreSQL/Redis 数据。

`dev.sh start` 会先启动数据服务；已使用外部数据服务时设置 `DEV_MARKET_DATA=0`。`dev.sh stop` 只停止业务开发进程，不停止数据服务，也不停止它依赖的 RSSHub，因此关闭业务页面或业务服务不会中断自动维护。需要停止数据服务时单独执行：

```bash
bash market_data_service/manage.sh stop
```

API 文档是 `http://127.0.0.1:8010/docs`；带 token 的环境可在自己的 HTTP 客户端中设置 `Authorization: Bearer ...`。不要把生产 token 写进浏览器 URL、日志或共享命令历史。

## 旧库迁移

只迁移 `stock_meta` 和 `stock_daily`。旧 SQLite 使用 `mode=ro` 打开，不修改原文件，不迁移旧缓存 pickle，不复制用户/任务表。保留原抓取时间、统一时区与历史成交量口径；非法数值记录进 `ImportIssue` 并隔离为空。导入的历史日线与财务数据不会直接认证为新鲜。

必须在**目标数据库尚未开始任何采集**时导入：

```bash
.venv-data/bin/python -m market_data_service.cli init
.venv-data/bin/python -m market_data_service.cli import-legacy /absolute/path/to/stock_analysis.db
bash market_data_service/manage.sh start
```

容器部署需要导入时，先只启动 `postgres redis`，再以只读 bind mount 将旧库提供给 `migrate` 一次性容器，执行相同的 CLI `import-legacy`，最后启动其余进程。不要把旧库挂载为数据服务的运行数据库。

导入以自然键 upsert，可在采集前重新执行以恢复中断；开始采集后再次导入会拒绝，避免历史数据覆盖新版本。本次现有工作区已导入 5,891 条证券记录及 2,444,521 条历史日线；这是迁移时数量，不代表当前市场数量或当前达标率。

原业务库中的旧市场表为可恢复存档，业务已不读写这些采集表。原有用户、自选与任务数据保留；不要为了“清理”而删除整个业务库。

## API 合约

| API | 用途 |
| --- | --- |
| `GET /v1/health` | 数据库可达、scheduler/source worker/sync worker 心跳、历史导入问题数量 |
| `GET /v1/capabilities` | 闭集来源操作及 Pydantic 生成的参数 JSON Schema |
| `GET /v1/datasets` | 十类数据、逐项覆盖汇总、策略和最近任务 |
| `GET /v1/events` | 带游标的变更推送、断线补发与快照重置 |
| `GET /v1/jobs/{id}/events` | 有界等待任务进度，不轮询任务明细 |
| `GET /v1/datasets/{id}/coverage` | 按状态/证券筛选、分页 |
| `GET /v1/datasets/{id}/coverage.csv` | 全部覆盖导出，防止来源文本触发 CSV 公式 |
| `PUT /v1/datasets/{id}/policy` | 保存启停、检查间隔、允许延迟；两种时间联合校验 |
| `GET/POST /v1/jobs` | 分页/按数据集查询，创建持久同步任务 |
| `GET /v1/jobs/{id}` | 分页明细、只看失败项、进度与错误 |
| `POST /v1/jobs/{id}/cancel`、`retry` | 安全取消、仅重试失败/未完成项 |
| `GET /v1/securities`、`calendar` | 维护后的证券身份与交易日历 |
| `POST /v1/snapshots` | 批量严格读取证券/财务/日线/新闻/行情，支持历史区间 |
| `POST /v1/observations` | 按已注册操作读取或订阅来源，不允许任意远程函数执行 |
| `GET /v1/observations/{version}` | 读取不可变历史版本；明确标记 historical |

日线单股每次最多 5,000 条，批量最多 20 万条；业务筛选自动分段。来源参数的类型、范围、未知字段和日线日期区间在入队前验证。同步任务 HTTP 202 表示已提交，不等于同步成功。

## 任务恢复与故障处理

Celery 采用至少一次投递，不宣称 exactly-once。PostgreSQL 活跃任务唯一约束、领取租约、行锁与提交前 fencing 保证重复交付不重复发布；每股结果独立持久化。来源刷新另用 Redis 原生锁防止同一订阅重叠请求。

- 每个数据集同一时间只有一个活跃全市场任务，重复提交返回已有任务。
- 排队任务可立即取消；运行任务等待当前上游请求结束，在下次持久化前检查取消标记，保留先前已发布数据。
- 采集进程中断后约 120 秒租约过期，调度恢复未完成项，不重做已成功项。Celery 的来源请求有界重试；全量任务 soft/hard 限制约 6 小时，Redis visibility timeout 7 小时。
- 进程管理向 Celery 主进程发送 QUIT，使用 [Celery 原生有界软关闭](https://docs.celeryq.dev/en/stable/userguide/workers.html#soft-shutdown)（20 秒）清理队列并回收子进程，再由持久租约恢复工作；不等待数小时的全市场任务全部结束才允许部署。
- 策略、订阅、任务和进度存数据库；刷新页面或重启 API 不会丢失。Beat 必须只部署一个；来源和全市场队列分别消费，避免长任务挤占短查询。
- 上游失败保留已有版本并记录错误；未达标项会继续按策略/重试时间补采。公共来源故障不能通过“清零错误”或把旧值改成 fresh 解决。
- `/v1/health` 中 API 的 `status=ok` 只表示服务和数据库可达；还要检查采集、全市场同步、调度和事件维护四个组件心跳的 `healthy`。页面会把缺失或过期心跳显示为“自动维护尚未就绪”。

建议监控队列积压、数据达标率、源错误、磁盘容量与数据库连接数。定期使用 PostgreSQL 原生备份，并备份 Redis AOF/配置和服务凭证。不可变版本可能被运行证据引用，当前不自动清理历史版本；清理必须先制定引用保留策略，不要直接按时间删除。

## 验证边界

定向测试在 `tests/market_data` 与相应业务/来源模块测试中，页面测试在 `DataMaintenanceSettingsView.test.tsx`。未执行全仓库测试或全仓库类型检查。

可重复的隔离 HTTP 端到端测试使用真实 PostgreSQL、Redis、Celery、FastAPI 和业务 HTTP 客户端，只将公共上游替换为显式 fixture。测试库名带 `market_data_e2e_` 前缀，业务为独立空 SQLite，队列使用专用 Redis DB 12。绝不会在真实业务库中制造错误数据，也不会调用大模型、下单或发送通知。

```bash
PYTHONPATH=. .venv-data/bin/python tests/market_data/e2e_stack.py start
MARKET_DATA_E2E=1 PYTHONPATH=. .venv-data/bin/python -m pytest tests/market_data/test_e2e.py -q
PYTHONPATH=. .venv-data/bin/python tests/market_data/e2e_stack.py stop
```

脚本默认使用本机独立 PG 5433；其他环境配置 `MARKET_DATA_E2E_PG_ADMIN`（拥有创建测试库权限）与 `MARKET_DATA_E2E_BUSINESS_PYTHON`。测试保留数据库以便复核，不自动删除库或卷。浏览器验收连接隔离业务 8001，独立测试前端 5174；覆盖桌面/390px 窄屏、策略、分页/搜索/导出、失败/重试、取消、离线与恢复。

本机实际运行与测试使用原生进程；Docker daemon 不可用，因此 Compose 配置可校验，但镜像构建和容器部署不作为已完成的运行验证。
