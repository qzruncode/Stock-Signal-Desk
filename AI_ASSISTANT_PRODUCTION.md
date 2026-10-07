# AI 助手生产部署

## 运行边界

生产聊天由应用内的 LangChain / LangGraph 运行时承载，并由 LangGraph 原生
Checkpointer 持久化状态。Direct 使用 `langchain.agents.create_agent` 的消息—工具循环，
Plan 使用规划协调器，Team 使用 supervisor/worker 图，Goal 使用独立目标图；Auto
通过模型选择已开放模式。它不依赖 LangGraph Server。各模式的部署与证据、审批、预算
边界共享，具体见 [运行架构](AGENT_ORCHESTRATOR_V4.md)及
[Plan / Team / Goal 契约](src/agent/langgraph_runtime/PLANNING.md)。

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

## PDF 知识库 RAG v1

RAG 使用独立的 Celery 应用与 `rag` 队列，不复用 `market_data_service` 的任务应用或数据库。
API、RAG worker 必须连接同一业务数据库，并访问同一个底层持久原件卷；数据库保存相对 blob key，
因此两端的 `RAG_STORAGE_PATH` 挂载点可以不同。Qdrant 与
Redis 的数据卷也必须纳入备份。支持文字层、扫描版及混合型 PDF（最多 50 MiB、500 页）；Docling
使用 RapidOCR ONNX 简体中文配置识别扫描页文字，并保留表格结构和页码定位。OCR 后文本仍不足、
扫描质量过低或页面主要由无文字图像组成时，文档会进入明确的 `unsupported` 状态，不会发布为可检索文档。
OCR 负责识字，不代表理解图表/示意图的语义；这类视觉内容需要单独的 VLM 或领域解析能力，当前不承诺完整覆盖。
Docling 是唯一 PDF 结构解析器，缺少依赖时入库任务会明确失败，不会换用其他解析器。Intel macOS
上的 PyTorch 不支持 Python 3.13；本机 RAG 启动脚本会使用 Python 3.11 和 `requirements-rag-worker-macos.lock`
创建隔离的 Celery worker 环境，从官方 PyPI 按哈希安装 Docling 及兼容的 PyTorch、NumPy、SciPy、OpenCV、cryptography 版本，避免
本机镜像缺少解析器发行包；同时固定 Transformers 4.57.6 与 Hugging Face Hub 0.36.0，避免 Transformers 5.x 与 Intel PyTorch 2.2.2 冲突。
该 Intel 兼容环境不是安全生产部署承诺：截至 2026-10-07，PyTorch 2.2.2 和 Transformers 4.57.6 仍有已知安全告警。
[PyTorch 官方已停止 Intel macOS 新版二进制发行](https://dev-discuss.pytorch.org/t/pytorch-macos-x86-builds-deprecation-starting-january-2024/1690)，
不能通过简单升级 pip 锁文件解决。生产优先使用下述 Linux worker 路径，并验证与 API 共享数据库及原件卷；不要对不可信 PDF 使用未修复的兼容 worker。
Docling 官方解析核心在 Intel Mac 上需从源码构建，首次安装前需具备 Xcode Command Line Tools；启动脚本会在缺少时明确提示，不会静默降级。其他平台的 worker 使用
项目主环境及 `requirements.lock`。两个环境都在 worker 启动前检查 Docling、RapidOCR、ONNX Runtime
及中文 OCR 配置。

### 本机原生开发服务（不使用 Docker）

```bash
bash scripts/rag-local.sh start
bash scripts/rag-local.sh status
```

脚本原生启动 Qdrant 官方 macOS 二进制（固定 1.19.1）、独立 AOF Redis（6382）、RAG 专用 Celery
worker/beat、本机 FastEmbed ONNX embedding 服务和 BGE reranker 服务；两个模型服务只监听回环地址。
Embedding 固定为多语言 MiniLM（384 维，FastEmbed 0.8.0/ONNX CPU），首次启动会下载固定 revision，校验
ONNX SHA-256 后再加载；推理服务使用独立 Python 3.11 虚拟环境，不依赖 Ollama、公司模型网关或 Docker。
Reranker 使用 `BAAI/bge-reranker-base`，优先复用 OpenViking/Hugging Face 缓存，缺少时下载固定版本。
原件、Qdrant、Redis 数据和 FastEmbed 权重缓存在 `data/rag/`；行情服务的 Celery app、队列和数据库不会
被复用或修改。停止本机 RAG 服务：

```bash
bash scripts/rag-local.sh stop
```

API 和 worker 的 `.env` 至少设置 `RAG_QDRANT_URL`、`RAG_CELERY_BROKER_URL`、
`RAG_STORAGE_PATH`、`RAG_EMBEDDING_BASE_URL`、`RAG_RERANK_BASE_URL`。本机默认地址为 embedding
`127.0.0.1:8081`、reranker `127.0.0.1:8082`、Qdrant `127.0.0.1:6333`、独立 Redis
`127.0.0.1:6382`。生产环境使用私有网络地址并为 Qdrant/Redis 启用鉴权和网络隔离；不要将未认证
服务暴露到公网。

### Worker 与 schema

先备份业务数据库、PDF 原件目录和 Qdrant 数据卷，再运行现有迁移入口：

```bash
python scripts/manage_database.py migrate
```

在与 API 相同的应用版本、Python 环境和配置下启动一个或多个 RAG worker：

```bash
python -m celery -A src.rag.worker:celery_app worker \
  --queues rag --concurrency "${RAG_WORKER_CONCURRENCY:-2}" --loglevel INFO
```

仅运行一个 beat 调度实例，用于重新分发 broker 故障或 worker 崩溃后遗留的业务任务：

```bash
python -m celery -A src.rag.worker:celery_app beat --loglevel INFO \
  --schedule "${RAG_STORAGE_PATH:-./data/rag}/celerybeat-schedule"
```

业务任务记录在主数据库中；Celery late-ack、幂等任务 ID、有限重试以及周期性 reconcile 负责恢复。
超过 worker 硬时限并留有恢复余量后，停在 `processing` 的任务会重新排队；重试前清除未发布版本
的残留向量。worker 数量和并发应同时受模型网关配额、CPU、解析内存和共享磁盘容量约束。API 与
worker 若未挂载同一底层原件卷，上传后虽能显示成功但后台无法解析，属于部署错误；不同运行环境
的文件系统挂载路径可以不同，但必须映射到同一持久化存储。

### 开源模型服务检查与恢复

Embedding / Reranker 独立于聊天模型网关配置。启动后先检查服务就绪，再分别发真实推理请求：

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8082/health
curl -fsS http://127.0.0.1:8081/api/embed \
  -H 'Content-Type: application/json' \
  -d '{"model":"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2","input":["PDF retrieval smoke test"],"dimensions":384}'
curl -fsS http://127.0.0.1:8082/rerank \
  -H 'Content-Type: application/json' \
  -d '{"query":"PDF retrieval smoke test","texts":["Relevant passage","Unrelated passage"],"truncate":true,"return_text":false}'
```

生产部署时将两个模型 ID 和 revision 固定为已验收版本。Embedding 模型或 revision 变化后，旧向量不得与
新查询向量混用：检索会明确返回 `index_rebuild_required`，需要在知识库页重建文档索引；Reranker
只负责候选重排，升级它不需要重建向量。知识库页顶部的“检查模型服务”会各发一条真实推理请求，
展示模型版本、向量维度、耗时和可操作的错误信息；无需再从聊天模型配置页测试这两种能力。

上传验收 PDF 后应确认文档由 `queued` 进入 `ready`、索引点数与 chunk 数一致，再用管理页检索
试验台验证中文关键词/语义召回、排序、文件名、页码与 PDF 跳转，并在聊天中验证引用与无证据答复。
《Agentic Design Patterns》PDF 的离线验收问题及物理页码锚点保存在
`tests/fixtures/rag-acceptance-agentic-design-patterns.json`；发布时应以同一 PDF、该问题集和
一份代表性中文文本型 PDF 实际执行 Recall@K、排序及引用页核对。验收集只保存问题和定位锚点，不复制原 PDF。
失败的解析/索引任务可从管理页重试；重建索引成功前继续使用旧活动版本。删除会立即将文档从
检索范围排除，再异步清理 Qdrant 与原件；若持续处于 `deleting`，查看任务错误后再次触发删除，
确保清理任务重新入队。数据库与 Qdrant 备份必须协调保存；若只恢复一侧，使用管理页重建索引，
不要将向量库里的孤立 payload 当成有效文档。
