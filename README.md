# Stock Assistant

A 股 / 港股 / 美股自选股 AI 研究工作台。通过研究对话、PDF 知识库、财经来源和指标筛选组织研究，并追踪工具调用、证据与任务状态。后端使用 FastAPI、LangChain / LangGraph，前端使用 React。

研究结果依赖模型和数据来源的覆盖、时效与准确性；证据检查不能保证结论正确。本项目不提供自动交易，也不应将生成内容直接作为交易指令。独立数据服务的全市场自动维护主要覆盖 A 股；港股、美股的可用数据以具体来源为准。

## 当前功能

- **AI 研究对话**：提供 Auto、Direct、Plan、Team、Goal 五种模式，支持流式回答、证据、审批、取消与恢复；Auto 默认选择 Direct / Plan / Team，自动选择 Goal 需单独开启
- **PDF 知识库**：上传、解析、索引与检索 PDF，在研究对话中引用文档页码；需要独立 RAG 服务，扫描件 OCR 不等于完整理解图表
- **任务追踪**：通过运行记录和 SSE 查看工具轨迹、证据与 AI 输出
- **Agent 提示词**：在 WebUI 中新建、编辑、删除和激活 Agent 提示词
- **自选股管理**：维护 `STOCK_LIST`，支持单条添加、批量添加、按市场分组展示和删除
- **模型配置**：在设置页维护模型 API Key、主模型和相关参数，保存后自动重载配置
- **可选推送**：通过通知工具发送研究过程中的自定义提醒
- **可选认证**：支持运行时开启管理员认证，未登录时跳转登录页
- **独立数据服务**：独立 PostgreSQL、自动采集与持久任务；业务/工具通过 API 读取，数据维护中心支持策略、时效、覆盖明细、取消及失败重试

## WebUI 页面

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/` | AI 研究对话 | 流式回答、证据、审批、取消与恢复 |
| `/stocks` | 股票与自选 | 复用股票列表与自选分组 |
| `/screening` | 指标选股 | 复用指标筛选和分组保存 |
| `/sources` | 财经来源 | RSS 来源与内容预览 |
| `/runs` | 运行记录 | 工具轨迹、证据与质量检查 |
| `/monitoring` | 系统监控 | 容量、依赖、估算预算与供应商实报 Token |
| `/knowledge` | PDF 知识库 | 上传、索引、检索、模型服务检查和页码引用 |
| `/setting` | 设置 | 模型、Prompt、工具、通知及数据维护；认证开启时未登录自动显示登录页 |

## 快速开始

### 环境要求

- Python 3.13：业务后端；本机 RAG 模型服务需要 Python 3.11，Intel Mac 的 Docling worker 也使用 Python 3.11
- Node.js 24：WebUI 构建与本机来源服务；版本见 `apps/dsa-web/.nvmrc`
- Node.js 22、pnpm、Redis 和 Rust 工具链：本机 Firecrawl 构建另有这些要求，见其[服务说明](services/firecrawl/README.md)；使用 nvm 时须分别安装 Node 22 和 24
- 独立 PostgreSQL / Redis：行情数据服务；可使用 Docker Compose 或自行部署
- 模型服务凭证：研究对话需要兼容的 Anthropic 接口

当前主环境锁文件分别针对 Linux x86_64 和 Intel macOS 生成，其他平台尚需验证安装兼容性。首次启动本机完整服务还会下载上游源码、浏览器及模型权重，需预留网络、磁盘和安装时间。

**Intel Mac PDF worker 的已知限制**：兼容锁文件仍依赖官方已停止提供新版 wheel 的 PyTorch 2.2.2，以及 Transformers 4.57.6；二者存在已知安全告警。不建议用该环境处理不可信 PDF 或作公网生产部署。部署者需自行评估风险，可参考现有 Linux worker 部署路径，详情见 [发布清单](RELEASE_CHECKLIST.md)。

### 安装依赖

```bash
python3.13 -m venv .venv
source .venv/bin/activate
# Linux x86_64：只执行这一条
python -m pip install --require-hashes -r requirements.lock

# Intel macOS：改为执行这一条，不与上面的安装叠加
python -m pip install --require-hashes -r requirements-macos.lock
# Intel Mac 的 RAG 启动脚本会再用 Python 3.11 和专用锁文件创建隔离 worker 环境

cd apps/dsa-web
# 如果使用 nvm，先运行 nvm use
npm ci
npm run build
cd ../..
```

前端构建输出到仓库根目录的 `static/`，该目录不提交到 Git。新克隆的仓库必须生成前端产物才能由后端托管完整 WebUI。

### 配置

```bash
cp .env.example .env
```

最少需要配置：

```dotenv
STOCK_LIST=600519,300750,002594
ANTHROPIC_AUTH_TOKEN=your_token
ANTHROPIC_MODEL=你的服务支持的模型ID
```

如使用中转服务，再配置 `ANTHROPIC_BASE_URL`。完整配置示例见 `.env.example`。

本机开发可使用默认业务 SQLite；生产使用独立业务 PostgreSQL。业务库与行情库必须分开。初始化或升级业务数据库后检查版本：

```bash
python scripts/manage_database.py migrate
python scripts/manage_database.py check
```

生产升级前先备份，具体顺序见 [生产部署说明](AI_ASSISTANT_PRODUCTION.md)。不要在生产数据库上直接套用本机开发步骤。

证券、行情、财务与资讯数据由独立数据服务维护。先复制 `.env.market-data.example` 为 `.env.market-data`，设置数据库密码、连接串和服务 token，再按 [数据服务部署与迁移说明](market_data_service/OPERATIONS.md) 启动独立 PostgreSQL/Redis 与采集服务；业务 `.env` 配置对应的 `MARKET_DATA_SERVICE_URL` 与 `MARKET_DATA_SERVICE_TOKEN`。服务离线或数据未达标时会明确提示，不回退到旧业务库。

财经 RSS 使用 [RSSHub](services/rsshub/README.md)；网页搜索与读取使用 [SearXNG](services/searxng/README.md) 和 [Firecrawl](services/firecrawl/README.md)。PDF 知识库需启动独立 Qdrant、Redis、Celery worker、embedding 和 reranker，配置及原生启动命令见 [RAG 部署说明](AI_ASSISTANT_PRODUCTION.md#pdf-知识库-rag-v1)。这些服务不由下方的业务后端命令单独启动。

## 运行

### 推荐：后端托管 WebUI

```bash
python main.py --serve-only --host 127.0.0.1
```

启动后访问：

- WebUI: `http://localhost:8000`
- API 文档: `http://localhost:8000/docs`
- 健康检查: `http://localhost:8000/api/health`

生产部署前必须先执行数据库迁移和 AI 助手预检；数据库版本、认证、限流、模型、安全边界或前端产物任一不合格都会阻止启动。运行状态与事件已持久化，可使用多个 API worker。详见 [Agent Orchestrator V4 架构](AGENT_ORCHESTRATOR_V4.md) 与 [AI 助手生产部署](AI_ASSISTANT_PRODUCTION.md)。

### 指定端口

```bash
python main.py --serve-only --port 9000
```

### 前端开发模式

```bash
# 终端 1：启动后端
python main.py --serve-only

# 终端 2：启动前端
cd apps/dsa-web
npm run dev
```

前端开发服务默认访问 `http://localhost:5173`。

### 一键管理脚本

准备好业务 Python 环境、Node 24、模型配置、独立行情数据库和 Redis 后，`dev.sh` 会启动业务、前端、RAG、RSSHub、Firecrawl 和 SearXNG。它需要本机来源服务及 RAG 的安装依赖，不能代替前面的首次配置。

脚本默认还会创建前端公网隧道。本机开发建议明确关闭：

```bash
DEV_TUNNEL=0 ./dev.sh start  # 启动本机完整开发服务
./dev.sh stop      # 停止业务，数据服务及 RSSHub 继续维护数据
./dev.sh restart   # 重启服务
./dev.sh status    # 查看运行状态
```

启动后：

- 后端: `http://localhost:8000`
- 前端: `http://localhost:5173`

`dev.sh start` 默认复用或启动已配置的数据服务（8010）；使用外部部署时设置 `DEV_MARKET_DATA=0`。数据服务单独停止/重启使用 `bash market_data_service/manage.sh stop|restart`，不会删除数据库。完整部署命令和首次旧库只读导入见 [运维说明](market_data_service/OPERATIONS.md)。

## 常用命令

```bash
# 安装离线验收依赖（已激活业务环境）
python -m pip install --require-hashes -r requirements-eval.lock

# Agent 架构检查与定向测试；完整离线范围见 GitHub Actions
python scripts/check_agent_architecture.py
python -m pytest -q tests/test_agent_architecture.py tests/test_langgraph_agent_runtime.py

# 前端构建
cd apps/dsa-web && npm run build

# 前端测试
cd apps/dsa-web && npm run test

# 前端 lint
cd apps/dsa-web && npm run lint
```

## 项目结构

```text
.
├── main.py                  # 命令行入口，负责启动服务和准备 WebUI 静态资源
├── server.py                # uvicorn 入口
├── api/                     # FastAPI 应用、路由和中间件
│   └── v1/
│       ├── endpoints/       # auth / agent / system / watchlist
│       └── schemas/         # API 入参和响应模型
├── apps/dsa-web/            # React + Vite 前端
│   └── src/
│       ├── pages/           # 对话、市场工作台、知识库、运行记录、监控与设置页面
│       ├── components/      # 对话、运行记录、自选股与设置组件
│       ├── api/             # 前端 API client
│       ├── hooks/           # 任务流、仪表盘状态、自动补全等 hooks
│       └── utils/           # 协议转换、证据展示与纯逻辑
├── market_data_service/     # 独立数据服务、采集适配器、迁移与部署
├── data_provider/           # 旧兼容模块与纯代码规范化工具；业务采集已迁出
├── src/                     # 分析、配置、LLM、搜索、通知、存储等核心逻辑
├── tests/                   # Python 测试
├── .env.example             # 环境变量模板
└── requirements.txt         # Python 依赖
```

## API 模块

当前 v1 API 挂载在 `/api/v1` 下：

- `/auth`：登录状态、登录、登出、初始密码设置
- `/agent`：研究对话、任务运行、提示词、质量与运行监控
- `/system`：系统配置读取、保存、导入导出、连接测试
- `/data-service`：数据服务健康、数据集、采集任务与任务进度
- `/rss`：财经来源与内容读取
- `/indicator-screening`：指标筛选与分组保存
- `/stocks`：股票搜索与基础信息
- `/watchlist`：自选股读取、添加、删除
- `/knowledge-bases`：PDF 知识库与文档管理

以运行中的 `/docs` 为完整接口说明。

## 架构与维护

- [Agent 运行架构](AGENT_ORCHESTRATOR_V4.md)：执行模式、持久化、审批与证据合同
- [Plan / Team 运行契约](src/agent/langgraph_runtime/PLANNING.md)
- [工具结果合同](src/tools/RESULT_CONTRACT.md)
- [生产部署与 RAG](AI_ASSISTANT_PRODUCTION.md)
- [数据服务运维](market_data_service/OPERATIONS.md)与[事件机制](market_data_service/EVENTS.md)
- [贡献说明](CONTRIBUTING.md)与[安全报告](SECURITY.md)
- [开源发布准备清单](RELEASE_CHECKLIST.md)：已完成检查、当前证据与待处理项

若启动失败，优先检查模型凭证、数据库 schema、数据服务健康与前端产物。PDF 上传后未就绪时检查 RAG worker、模型服务与共享原件目录；API 存活不代表这些依赖全部就绪。

## License

本仓库代码沿用 [MIT License](LICENSE)，保留原作者声明。通过安装脚本下载的 Firecrawl、SearXNG、RSSHub 源码不属于本仓库的 MIT 授权范围，分别遵循其上游许可证；修改、部署或再分发时须保留并核对相应条款，见 [第三方来源说明](THIRD_PARTY_NOTICES.md)。
