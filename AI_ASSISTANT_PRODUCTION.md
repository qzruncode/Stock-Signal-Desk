# AI 助手生产部署

当前助手使用一套强类型编排主线。Run、Step、副作用 Outbox、流事件、取消信号、租约、限流、熔断和资源配额均以数据库为权威状态源；API 进程只保存当前连接所需的短期对象。因此可以运行多个 API worker，客户端刷新或切换 worker 后仍能从持久事件游标续流。

## 1. 必要基础设施

生产环境使用 PostgreSQL，不允许把 SQLite 当作默认生产数据库。建议：

- PostgreSQL 主实例启用自动备份、WAL 归档或云厂商 PITR，保留期至少 7 天；
- 至少一次在隔离环境完成“备份 → 恢复 → schema check → 冒烟测试”的恢复演练；
- 模型网关、行情源和搜索服务有独立超时、容量与告警；
- API、迁移任务和备份任务使用不同数据库账号，遵循最小权限。

核心配置示例：

```dotenv
APP_ENV=production
DSA_PRODUCTION=true
DATABASE_URL=postgresql+psycopg://agent:replace-me@postgres:5432/agent
DATABASE_AUTO_MIGRATE=false
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT_SECONDS=30
DATABASE_POOL_RECYCLE_SECONDS=1800
DATABASE_STATEMENT_TIMEOUT_MS=30000
DATABASE_LOCK_TIMEOUT_MS=5000

ANTHROPIC_BASE_URL=https://your-anthropic-compatible-gateway.example.com
ANTHROPIC_AUTH_TOKEN=replace-with-secret
ANTHROPIC_MODEL=claude-sonnet-4-6

ADMIN_AUTH_ENABLED=true
AGENT_REQUESTS_PER_MINUTE=30
AGENT_MAX_ACTIVE_RUNS=12
AGENT_ISOLATE_ALL_STATELESS=true
AGENT_PLANNER_VERIFIER_MODE=enforce
AGENT_TRACE_ENCRYPTION_KEY=replace-with-valid-fernet-key

WEB_CONCURRENCY=3
UVICORN_WORKERS=3
CORS_ALLOW_ALL=false
WEBFETCH_ALLOW_PRIVATE=false
```

使用以下命令生成 trace 加密密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

多租户环境必须由可信反向代理覆盖身份头，并额外设置：

```dotenv
AGENT_MULTI_TENANT_ENABLED=true
TRUSTED_IDENTITY_HEADERS=true
TRUSTED_PROXY_IDENTITY=true
TRUSTED_IDENTITY_SHARED_SECRET=replace-with-at-least-32-random-characters
```

反向代理必须覆盖 `X-DSA-Tenant-Id`、`X-DSA-User-Id` 和 `X-DSA-Identity-Secret`；共享密钥只存在于代理与应用的密钥管理系统中。应用不会直接接受公网客户端伪造的身份头，健康检查豁免身份头但不承载租户数据。

## 2. 发布顺序

每次发布严格执行：

```bash
python -m pip install -r requirements.txt
python scripts/manage_database.py backup --output /backups/pre-release.dump
python scripts/manage_database.py migrate
python scripts/manage_database.py check

cd apps/dsa-web
npm ci
npm run build
cd ../..

python scripts/agent_production_preflight.py
python scripts/agent_tool_audit.py
pytest -q
```

迁移是独立 release job。生产 worker 的 `DATABASE_AUTO_MIGRATE=false` 会令启动只检查 schema 版本，避免多个副本并发执行 DDL。升级程序必须先向后兼容旧 worker，再滚动新 worker；破坏性字段删除放在后续版本。

数据库运维入口：

```bash
python scripts/manage_database.py pitr-check
python scripts/manage_database.py backup --output /backups/agent-20260730.dump
python scripts/manage_database.py restore --input /backups/agent-20260730.dump --confirm RESTORE
```

恢复是破坏性操作，只能在停止写流量、确认目标实例和保留当前备份之后执行。PostgreSQL 的时间点恢复由数据库平台完成；`pitr-check` 只检查可见的 WAL 设置，不能替代真实恢复演练。

## 3. 运行与网络

```bash
uvicorn server:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 3 \
  --proxy-headers
```

反向代理必须关闭 SSE 缓冲，读取超时大于 `AGENT_RUN_DEADLINE_SECONDS`，限制请求体并覆盖可信转发头。不要启用 `--reload`。优雅停机时间应大于一个事件持久化周期；worker 退出时会释放运行租约，其他 worker 随后接管。

数据库最大连接数按 `worker 数 × (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)` 预留并保留迁移、备份和运维余量；连接池等待超时必须小于请求总 deadline。

## 4. 可靠性合同

- 一次会话最多一个活跃 Run，数据库唯一约束负责仲裁跨 worker 竞争。
- 每个 Run 有总 deadline；Planner、模型流和 Tool 还有各自 deadline。
- 只读 Tool 仅对声明的瞬时错误做有限指数退避；非只读 Tool 不自动重试。
- Step 的幂等键会落库。完成结果可复用，运行中 Step 由租约保护，租约过期后才允许接管。
- 副作用 Tool 在执行前写入 Outbox。下游服务仍必须接受同一幂等键，才能覆盖“对方已成功、本地确认前崩溃”的最后窗口。
- 模型、Tool 和外部服务均受共享资源槽、熔断器和 Run 级调用量/token/成本预算约束。
- transcript、结构化上下文、artifact、trace 和 Run 终态在同一事务提交，失败不会发布半套终态。
- 每个流 chunk 先持久化再广播；客户端通过事件序号续流，不依赖原 worker 内存。
- 外部网页与 Tool 证据始终包在不可信数据边界中，模型不得执行其中指令。
- trace 写入前会按键名与内容模式脱敏；生产默认不保存原始 Planner 输出，结构化计划和结果使用 Fernet 加密，并按 TTL 清理。

## 5. 语义质量门禁

Planner 只生成强类型 Intent，不接触 Tool 名。程序随后完成参数校验、固定 Workflow 编译和 Policy 验证。生产模式必须启用第二个语义校验器；校验器输出置信度、问题列表和建议处置：

- `enforce`：低置信度或语义冲突直接阻断；
- `shadow`：记录结果但不影响执行，只用于发布前对比；
- `off`：仅允许本地开发。

模型、Prompt、Intent schema 或 Workflow 变更必须先跑黄金语料集，对任务分类、证券范围、依赖关系、影响等级和“证据不足时 fail-closed”分别计分。先 shadow，再小流量 canary，最后全量；失败率、规划阻断率或用户重试率超过基线即回滚。

```bash
python scripts/evaluate_agent_planner.py
python scripts/evaluate_agent_planner.py --max-cases 5
```

## 6. 健康、指标和告警

- `/api/health`：进程存活；
- `/api/v1/agent/readiness`：schema、注册表、容量和可选深度依赖探测；
- `/api/v1/agent/metrics`：结构化运行指标和当前告警；
- `/api/v1/agent/metrics/prometheus`：Prometheus 文本格式。

至少建立以下 SLO：

- Run 成功率与部分成功率；
- p50/p95/p99 总耗时；
- Planner、模型和 Tool 分阶段耗时；
- 恢复次数、重复 Step 复用次数、租约过期数；
- 熔断器打开数、资源等待超时数、预算拒绝数；
- 流断连率、续流成功率、终态原子提交失败数。

告警分级：

- P1：终态事务失败、跨租户访问、schema 不兼容、事件序号缺口；
- P2：成功率低于 SLO、p95 超阈值、熔断持续打开、恢复积压；
- P3：单一可选数据源降级或缓存命中率异常。

## 7. 上线验收与故障注入

发布前必须自动回放：

- 普通问答、实时行情、财务、估值、新闻、产业链、多股批处理和八维买入判断；
- 同义改写、跨轮引用、编辑后重发、刷新续流、停止生成和重复提交；
- Planner 低置信度、Prompt 注入证据、数据覆盖不足、空结果和 schema 错误；
- 模型超时/429/5xx、Tool 超时/进程崩溃、数据库短暂失败；
- worker 在规划中、Tool 中、终态提交前退出后的接管；
- 相同幂等键并发执行、副作用 Outbox 已完成后的重放；
- 多租户越权读取、取消和续流；
- 容量、限流、token 与成本预算耗尽；
- 备份恢复和 PostgreSQL PITR 演练。

压力测试至少覆盖目标并发两倍、持续 30 分钟，确认数据库连接池、事件表增长、SSE 连接数和外部服务并发均有明确上限。测试通过只表示本次构建满足门禁，不替代线上 canary 与回滚策略。

仓库内提供了有界黑盒压测入口；先在隔离或 canary 环境验证小流量，再逐步提高并发：

```bash
python scripts/load_agent_runtime.py \
  --base-url http://127.0.0.1:8000 \
  --requests 40 \
  --concurrency 8
```
