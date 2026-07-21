# AI 助手生产部署

AI 助手的运行中续流、停止和刷新恢复状态保存在 API 进程内。当前生产合同是“单 ASGI worker + 反向代理”，不是多 worker。应用启动时会执行同一套 fail-closed 预检；不满足条件会直接拒绝启动，避免服务看似在线但停止/续流随机失效。

## 1. 准备配置

复制 `.env.example` 为 `.env`，至少设置：

```dotenv
APP_ENV=production
DSA_PRODUCTION=true

ANTHROPIC_BASE_URL=https://your-anthropic-compatible-gateway.example.com
ANTHROPIC_AUTH_TOKEN=replace-with-secret
ANTHROPIC_MODEL=claude-sonnet-4-6

ADMIN_AUTH_ENABLED=true
AGENT_MAX_ACTIVE_RUNS=4
AGENT_REQUESTS_PER_MINUTE=30
WEB_CONCURRENCY=1
UVICORN_WORKERS=1

CORS_ALLOW_ALL=false
WEBFETCH_ALLOW_PRIVATE=false
TRUST_X_FORWARDED_FOR=true
```

如果 WebUI 与 API 同域，不需要配置 `CORS_ORIGINS`。只有分离部署时才填写精确的 HTTPS 来源，不要使用 `*`。

首次上线前初始化管理员密码：

```bash
python -m src.auth reset_password
```

`data/.admin_password_hash`、`data/.session_secret`、数据库和 RSS 缓存目录必须放在持久卷中，权限仅授予服务账号。不要把 `.env`、密码文件或令牌写入镜像与版本库。

## 2. 构建与预检

```bash
python -m pip install -r requirements.txt
cd apps/dsa-web
npm ci
npm run build
cd ../..
python scripts/agent_production_preflight.py
python scripts/agent_tool_audit.py
```

预检同时验证：生产认证、管理员密码、模型配置、单 worker、请求限流、危险网络开关、前端产物、数据库和工具注册表。输出 `"ok": true` 后，再运行全工具审计；审计必须显示注册数与用例数一致且全部通过后才能启动。启用认证的环境可通过 `DSA_SESSION_COOKIE` 临时环境变量提供已登录会话值，不要把它写入命令历史或配置文件。

## 3. 启动

```bash
uvicorn server:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers
```

由 systemd、Supervisor 或容器编排器托管进程，配置自动重启、资源上限和日志采集。不要使用 `--reload`，也不要增加 worker 数；要横向扩容必须先把运行注册表、广播与取消信号迁移到 Redis 等共享基础设施。

## 4. 反向代理

只暴露 HTTPS 反向代理，不直接暴露 Uvicorn。代理需要：

- 关闭 SSE 响应缓冲和压缩缓存；
- 将读取超时设为大于最长模型任务时间（建议至少 15 分钟）；
- 覆盖并传递可信的 `X-Forwarded-For`、`X-Forwarded-Proto`；
- 限制请求体大小，并保留 `/api/v1/agent/chat` 的流式传输；
- 对登录、AI 对话和工具试运行端点增加边缘限流。

`TRUST_X_FORWARDED_FOR=true` 仅适用于应用只接受可信单层反向代理连接的拓扑。直连公网时必须为 `false`。

## 5. 监控与验收

- `/api/health`：进程存活检查，可供负载均衡器使用；
- `/api/v1/agent/readiness`：需登录的 AI 助手就绪检查，包含数据库、模型、运行容量和工具注册数量；
- 日志中的 `[AgentRun]`：包含 `run_id`、状态、耗时和流式 chunk 数；
- 工具日志：包含成功/部分成功/兜底、条目数和耗时，不记录鉴权令牌。

上线前至少回放以下场景：普通问答、实时行情、财务与估值、产业链研究、财经资讯零结果、工具上游失败、刷新续流、停止生成、重复提交、超限与 429/503。确认失败时页面展示具体原因，不能把“上游失败”伪装成“没有数据”。

AI 助手的任务执行还必须满足以下生产合同：

- Planner 只接收本轮最后一条用户问题、紧邻上一回答的结构化提纲和标准子任务目录，不能看到更早的用户目标，也不能看到或选择任何数据 Tool。上一回答只用于解析“上面、这些、继续”等指代，不能把已经完成的旧问题重新创建成任务。Planner 只负责拆分任务、提取语义参数、声明依赖和确认状态；普通用户措辞不得由运行时代码直接选择数据 Tool。唯一的安全收窄例外是：当本轮或紧邻上一回答已有经过本地证券库核验的完整证券集合，且用户明确询问“哪些现在能买入”时，程序直接生成 `investment_decision` 标准任务，避免 Planner 超时或漂移；它仍不能选择 Tool，后续只能进入固定 Workflow。
- 每个标准子任务必须由只读的 `Workflow Registry` 编译成不可修改的固定流程。单个流程只允许 0～8 个白名单 Tool；模型输出中即使出现 Tool 名，也不得改变白名单、步骤顺序或调用预算。新增 Tool 若未归入固定流程，生产预检必须失败。
- 所有调用必须在 UI 展示前通过 Policy Validator：检查 Tool 归属、前置任务和步骤、JSON Schema 参数、重复调用、已有结果复用、次数上限、影响等级和用户确认。任一项失败都应在调用前拦截。
- 搜索/读取/删除、查询/发送等复合任务必须在 Registry 中声明动作级条件参数；例如读取报告必须先有 `record_id`、发送自定义通知必须先有正文。不得把这些条件留到 Tool 内部执行后才报错。
- 无依赖的读取任务并行执行，有依赖的任务按 DAG 顺序执行；相同 Tool 与相同参数在一轮内只执行一次，其他任务复用同一结果。任何前置任务失败时，后续任务必须标为跳过，不能继续猜测执行。
- 删除、通知、计划修改等高影响操作必须由独立确认规则拦截。交易请求只能进入“参数校验 → 账户检查 → 风控检查 → 用户确认 → 下单 → 订单状态”状态机；当前未接入账户、风控和下单 Tool，因此必须明确拒绝执行，不能降级成普通 Tool 调用。
- “按上文某个方向找 A 股公司”必须被拆为 `theme_stock_discovery`，只调用内部结构化领域候选流程；只有用户明确把订单、客户、量产、收入等经营事实设为公司纳入或剔除条件时，才使用单独的业务举证流程。
- Planner 网关超时时，先使用更小的紧邻上下文恢复一次；验证通过的任务计划持久缓存 7 天。重复请求和重新生成直接复用缓存，编辑后的问题必须生成不同缓存键。两次超时都失败时，不得开放任何数据 Tool。
- 多股财务筛选按每批最多 12 只执行，单任务最多 8 批；所有批次均需进入同一固定 Workflow。最终结果必须给出总数、成功覆盖数与缺失数，任一批失败时列出未覆盖股票。
- `investment_decision` 只允许调用九项严格买入判断工具。底层工具单次最多 8 只，Agent 为避免长分析批次超时固定按每批 2 只覆盖完整集合（最多 300 只）。每只股票固定执行“主线真实受益 → 产业竞争力 → 三年空间 → 景气上行 → 非内卷 → 6—12 个月催化 → 重大风险 → 估值及利好透支 → 买入位置与风险收益比”；首项不通过立即停止，只有九项全部通过才可输出“可买入”。分业务收入或利润未披露时可用订单、销量、客户、产能、量产和连续增速替代核验；可买入项必须输出仓位、止损和失效条件。任何分批缺失、数据异常或分析失败均不得输出买入结论。

对应的上线回放至少包含：普通问答不开放 Tool、估值与新闻组合拆题、重复财务数据只查询一次、依赖失败阻止后续任务、删除/通知未确认拦截、交易请求固定状态机拦截、引用上轮第一梯队只走内部领域候选、超过 48 只股票的完整分批筛选、上轮股票集合“哪些现在能买入”的九项逐股否决，以及任一批次失败的故障注入。

RSSHub、SearXNG、Firecrawl 等可选数据服务应作为独立受监控服务部署。它们不可用时 AI 助手会暴露来源失败或使用明确标注的兜底；就绪检查不把可选外部源的瞬时抖动当作进程故障。
