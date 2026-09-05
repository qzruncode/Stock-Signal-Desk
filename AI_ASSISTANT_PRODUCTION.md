# AI 助手生产部署

## 运行边界

生产聊天由应用内的 `langchain.agents.create_agent` 承载，并由 LangGraph 原生
Checkpointer 持久化消息—工具循环。它不依赖 LangGraph Server，也没有 Planner、固定
Workflow、能力注册表或按意图切换的业务模板。

PostgreSQL 是生产数据库；SQLite 仅用于本地。所有 API worker 使用同一个数据库、原生
Postgres Checkpointer、运行租约和事件表，因此刷新页面或切换 worker 后可从事件游标续流。

```dotenv
APP_ENV=production
DSA_PRODUCTION=true
DATABASE_URL=postgresql+psycopg://agent:replace-me@postgres:5432/agent
DATABASE_AUTO_MIGRATE=false

AGENT_CHECKPOINT_DATABASE_URL=postgresql://agent:replace-me@postgres:5432/agent
AGENT_MAX_ACTIVE_RUNS=12
AGENT_REQUESTS_PER_MINUTE=30
AGENT_MAX_TOOL_CALLS=1000
AGENT_MAX_PROVIDER_CALLS=64
AGENT_MAX_ESTIMATED_TOKENS=1000000
AGENT_MAX_ESTIMATED_COST_MICROS=5000000
AGENT_EVIDENCE_REPAIR_LIMIT=2
AGENT_RESPONSE_REPAIR_LIMIT=1
AGENT_SOURCE_FALLBACK_REPAIR_LIMIT=1
AGENT_ISOLATE_ALL_STATELESS=true
AGENT_TRACE_ENCRYPTION_KEY=replace-with-valid-fernet-key

WEB_CONCURRENCY=3
UVICORN_WORKERS=3
CORS_ALLOW_ALL=false
WEBFETCH_ALLOW_PRIVATE=false
```

调用量、Token、成本、并发和工具层重试可以受预算控制；不得给模型思考、候选回答生成或
整轮分析设置本地超时。用户取消、真实上游异常、进程退出和工作预算耗尽才是终止条件。

## 发布顺序

1. 在独立 release job 中安装锁定依赖、备份数据库并执行迁移；生产 worker 只检查 schema。
2. 在部署前创建原生 LangGraph Checkpointer 表。
3. 运行静态架构检查、受影响的后端测试、前端类型/构建检查和浏览器冒烟；完整回归由 CI 执行。
4. 启动新 worker 后，将旧引擎未完成运行标记为 `legacy_engine_cutover` 取消状态。旧历史只读，
   不恢复或转换旧检查点。
5. 逐步放量并监控完成率、partial 比例、工具失败后的替代来源选择、审批成功率、终态事务
   失败、续流成功率与证据审计缺口。

## 可靠性与安全合同

- 一次会话最多一个活跃 Run；数据库唯一约束与租约协调跨 worker 竞争。
- 每个原子工具调用都经过 schema 校验、隔离执行、结构化结果归一化和幂等账本。
- 只读调用可并行；副作用严格串行，必须经 `interrupt()`、服务端批准和调用指纹后才执行一次。
- 结果合同区分“调用成功”和“数据可用”：失败、空结果、过期或时效未知不会进入可引用证据。
  已声明安全来源链的 operation 在自身边界内完成结构化切换并记录每次尝试；仍未恢复的来源缺口
  会被 Agent 中间件拦截，要求模型在有限次数内显式执行 `search_web_source(source_id=auto)`
  或 `read_web_source(source_id=auto)`。网页 operation 自己负责 HTTP/provider 级切换，禁止隐藏嵌套调用。
- 最终回答中每个外部事实都绑定 `ev_...` 证据。发布前会检查工具成功、来源、实体范围和
  时间口径；无法修复时发布 `partial` 并明确缺口。
- 事件先持久化后广播；最终消息、运行状态和 terminal 事件在同一事务提交。
- Trace、工具参数和审批展示必须脱敏。网页与来源返回内容是非可信数据，不能覆盖系统指令。

## 运维与恢复

反向代理需关闭 SSE 缓冲与 Agent 流读取截止时间，并限制请求体。优雅停机应允许当前事件写入
完成；worker 退出后，其租约可被其他 worker 接管。模型客户端、数据库、来源服务和工具
进程分别监控容量、错误率与熔断状态。

检查点恢复会继续同一 `thread_id` 的通用循环；失去首个新引擎检查点的运行会按原用户消息从
新版图重新开始。历史 V2 结构化 JSON 只能用于展示，绝不能送入新图。

## 上线验收

- 无工具的通用问答；
- 需要最新外部材料的多来源取证；
- 主来源失败后，安全的结构化 operation 自动完成声明式来源切换；仍未恢复时必须经过一次有界的
  网页搜索/读取兜底，兜底失败则发布 `partial` 并说明缺口；
- 没有相近模板的长尾问题；
- 副作用的批准、拒绝、重复批准和重启恢复；
- 用户取消、断线续流、同会话并发冲突和终态原子提交；
- 事实—证据映射中的来源、实体和时间不匹配；
- 前端紧凑过程展示、工具单行观察和审批卡片的刷新恢复。
