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

AI 助手的研究路由还必须满足以下生产合同：

- 先由语义模型输出结构化意图、引用范围、已解析领域和可执行筛选条件；运行时不得通过用户措辞的关键词、正则或同义词表猜测工作流。
- “按上文某个方向找 A 股公司”默认只查询同步到本地的结构化股票池与板块数据；只有用户明确要求核验订单、客户、量产、收入等公司级经营证据时，才进入资讯、研报与网络取证。
- 上文引用必须由语义层解析成明确领域后才能调用工具。解析失败时应停止并说明无法可靠理解，不能偷偷切换到网络搜索。
- 语义网关超时时，先使用更小的紧邻上下文执行一次语义恢复；验证通过的结构化结果持久缓存 7 天。重复请求和重新生成应直接复用缓存，编辑后的问题必须生成不同缓存键。两次网关调用都超时时，页面应明确显示“语义解析服务超时”，不能误报为用户问题无法理解。
- 多股财务筛选可以分批执行，但所有批次都计入本轮工具预算。最终结果必须给出总数、成功覆盖数与缺失数；任一批次失败时必须列出未覆盖股票，不能把缺失数据当成不满足筛选条件。

对应的上线回放至少包含：换一种说法表达同一意图、引用上轮第一梯队、单领域和多领域公司映射、超过 48 只股票的财务筛选，以及任一批次失败的故障注入。

RSSHub、SearXNG、Firecrawl 等可选数据服务应作为独立受监控服务部署。它们不可用时 AI 助手会暴露来源失败或使用明确标注的兜底；就绪检查不把可选外部源的瞬时抖动当作进程故障。
