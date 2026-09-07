# Stock Assistant

A 股 / 港股 / 美股自选股 AI 分析工作台。项目由 FastAPI 后端和 React WebUI 组成，用于管理自选股、选择分析模板、发起单股或批量分析，并追踪任务输出与历史报告。

## 当前功能

- **单股 AI 分析**：输入股票代码或名称，选择提示词模板后发起实时分析
- **任务追踪**：通过 SSE 查看运行中的分析任务，支持查看完整 prompt 与 AI 输出
- **历史报告**：查看历史分析记录，支持重新分析、查看 Markdown 全文和批量删除历史
- **提示词模板**：在 WebUI 中新建、编辑、删除、设为默认模板，并用于单股 / 批量分析
- **自选股管理**：维护 `STOCK_LIST`，支持单条添加、批量添加、按市场分组展示和删除
- **批量分析**：对当前自选股列表跑批，查看跑批进度、跑批记录和汇总报告
- **定时跑批**：在 WebUI 中配置每日跑批时间点和使用的提示词模板
- **模型配置**：在设置页维护模型 API Key、主模型和相关参数，保存后自动重载配置
- **可选推送**：分析时可开启企业微信推送；跑批完成后也会尝试发送通知
- **可选认证**：支持运行时开启管理员认证，未登录时跳转登录页
- **独立数据服务**：独立 PostgreSQL、自动采集与持久任务；业务/工具通过 API 读取，数据维护中心支持策略、时效、覆盖明细、取消及失败重试

## WebUI 页面

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/` | AI 研究对话 | 流式回答、证据、审批、取消与恢复 |
| `/research` | 研究档案 | 结论与当时证据、5/20/60 个交易日参考表现、可选变化提醒 |
| `/stocks` | 股票与自选 | 复用股票列表与自选分组 |
| `/screening` | 指标选股 | 复用指标筛选和分组保存 |
| `/sources` | 财经来源 | RSS 来源与内容预览 |
| `/runs` | 运行记录 | 工具轨迹、证据与质量检查 |
| `/monitoring` | 系统监控 | 容量、依赖、估算预算与供应商实报 Token |
| `/setting` | 设置 | 模型、Prompt、工具、通知及数据维护；认证开启时未登录自动显示登录页 |

## 快速开始

### 环境要求

- Python 3.13（锁文件验收版本）
- Node.js 24（本地开发或重新构建 WebUI）

### 安装依赖

```bash
python3.13 -m venv .venv
source .venv/bin/activate
# Linux x86_64；Intel macOS 改用 requirements-macos.lock
python -m pip install --require-hashes -r requirements.lock

cd apps/dsa-web
npm ci
cd ../..
```

锁文件更新、离线评测、迁移与定向验收见 [研究工作区维护说明](RESEARCH_WORKSPACE.md)。

### 配置

```bash
cp .env.example .env
```

最少需要配置：

```dotenv
STOCK_LIST=600519,300750,002594
ANTHROPIC_AUTH_TOKEN=your_token
ANTHROPIC_MODEL=claude-sonnet-4-6
```

如使用中转服务，再配置 `ANTHROPIC_BASE_URL`。完整配置示例见 `.env.example`。

证券、行情、财务与资讯数据由独立数据服务维护。运行业务前，按 [数据服务部署与迁移说明](market_data_service/OPERATIONS.md) 准备独立 PostgreSQL/Redis 并启动服务；业务 `.env` 配置 `MARKET_DATA_SERVICE_URL` 与 `MARKET_DATA_SERVICE_TOKEN`。服务离线或数据未达标时会明确提示，不回退到旧业务库。

## 运行

### 推荐：后端托管 WebUI

```bash
python main.py --serve-only
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

使用 `dev.sh` 可一键启动 / 停止前后端开发服务：

```bash
./dev.sh start     # 启动前后端
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
# Python 测试
pytest

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
│       ├── endpoints/       # auth / analysis / history / system / prompts / batch / watchlist
│       └── schemas/         # API 入参和响应模型
├── apps/dsa-web/            # React + Vite 前端
│   └── src/
│       ├── pages/           # Home / Watchlist / Settings / Login 页面
│       ├── components/      # 任务、历史、报告、自选股、模板、批量分析等组件
│       ├── api/             # 前端 API client
│       ├── hooks/           # 任务流、仪表盘状态、自动补全等 hooks
│       └── stores/          # 批量分析、自选股等状态管理
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
- `/analysis`：提交分析任务、查询任务状态、任务 SSE、手动推送分析结果
- `/history`：历史报告列表、详情、Markdown、关联新闻和删除
- `/system`：系统配置读取、保存、导入导出、连接测试
- `/prompts`：提示词模板管理
- `/batch`：批量分析、跑批记录、汇总报告、定时跑批配置
- `/watchlist`：自选股读取、添加、删除

## License

MIT License，详见 [LICENSE](LICENSE)。
