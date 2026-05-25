# Stock Signal Desk

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

## WebUI 页面

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/` | 选股通知工作台 | 单股分析、任务列表、历史报告、自选股面板、批量分析面板、模板管理 |
| `/portfolio` | 自选股列表管理 | 添加、批量添加、筛选、分组展示、删除自选股 |
| `/settings` | 模型 API 配置 | 维护模型配置并保存到 `.env` |
| `/login` | 登录页 | 仅在认证开启时使用 |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（需要本地开发或重新构建 WebUI 时使用）

### 安装依赖

```bash
pip install -r requirements.txt

cd apps/dsa-web
npm install
cd ../..
```

### 配置

```bash
cp .env.example .env
```

最少需要配置：

```dotenv
STOCK_LIST=600519,300750,002594
GEMINI_API_KEY=your_key
```

也可以使用 DeepSeek、OpenAI / OpenAI-compatible、Anthropic、Moonshot、DashScope、Ollama 等渠道。多渠道配置使用 `LLM_CHANNELS`，具体示例见 `.env.example`。

## 运行

### 推荐：后端托管 WebUI

```bash
python main.py --serve-only
```

启动后访问：

- WebUI: `http://localhost:8000`
- API 文档: `http://localhost:8000/docs`
- 健康检查: `http://localhost:8000/api/health`

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
./dev.sh stop      # 停止服务
./dev.sh restart   # 重启服务
./dev.sh status    # 查看运行状态
```

启动后：

- 后端: `http://localhost:8000`
- 前端: `http://localhost:5173`

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
├── data_provider/           # 行情和基本面数据源适配器
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
