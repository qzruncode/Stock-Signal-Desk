# Stock Signal Desk

A股自选股智能分析系统 — 基于多数据源 + LLM 的自动化股票信号分析平台。

## 功能概览

- **多数据源聚合** — efinance / akshare / tushare / pytdx / baostock / yfinance / longbridge / tickflow，按优先级自动切换
- **LLM 智能分析** — 支持 Gemini / DeepSeek / Claude / GPT / 通义千问 / Kimi / Ollama 等数十种模型，单 Key 或多渠道轮询 + fallback
- **实时行情** — 腾讯财经 / 新浪 / 东财多源量比、换手率、PE、PB 实时数据
- **15 种内置策略** — 多头趋势、均线金叉、放量突破、缩量回踩、缠论、波浪理论、情绪周期等
- **Multi-Agent 编排** — 单 Agent 快速分析 / 多 Agent 协作（技术→情报→风控→决策）
- **新闻搜索** — Tavily / SerpAPI / Brave / SearXNG / 博查 / MiniMax 多引擎聚合
- **企业微信推送** — 分析结果自动推送到群机器人
- **Web 管理界面** — FastAPI + 前端，在线配置模型、管理自选股、查看报告
- **图片识别** — 拍照识别股票代码，一键加入自选

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（Web 前端构建，可选）

### 安装

```bash
git clone git@github.com:qzruncode/Stock-Signal-Desk.git
cd Stock-Signal-Desk
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，至少填入：
#   STOCK_LIST=600519,300750,002594    # 自选股代码
#   GEMINI_API_KEY=xxx                 # 或其他 LLM Key
```

`.env.example` 中有完整的配置说明，包括所有支持的 LLM 渠道、搜索引擎、数据源等。

### 运行

```bash
# 启动 Web 服务（推荐）
python main.py --serve

# 仅启动 API 服务
python main.py --serve-only

# 指定端口
python main.py --serve --port 9000

# 调试模式
python main.py --debug --serve
```

启动后访问 `http://localhost:8000` 打开管理界面，API 文档在 `/docs`。

## 项目结构

```
├── main.py                  # 主入口
├── server.py                # FastAPI 应用入口
├── api/                     # API 层
│   ├── app.py               # FastAPI 实例
│   └── v1/                  # v1 版本接口
├── src/                     # 核心业务逻辑
│   ├── analyzer.py          # 分析引擎
│   ├── ai_caller.py         # LLM 调用封装
│   ├── config.py            # 配置管理
│   ├── notification.py      # 通知分发
│   ├── search_service.py    # 新闻搜索
│   ├── market_context.py    # 大盘复盘
│   ├── llm/                 # LLM 渠道与参数管理
│   ├── services/            # 业务服务层
│   ├── data/                # 股票代码映射
│   ├── notification_sender/ # 通知发送（企业微信等）
│   ├── repositories/        # 数据访问层
│   └── schemas/             # 数据模型
├── data_provider/           # 数据源适配器
│   ├── efinance_fetcher.py  # 东方财富
│   ├── akshare_fetcher.py   # AkShare
│   ├── tushare_fetcher.py   # Tushare Pro
│   ├── pytdx_fetcher.py     # 通达信
│   ├── baostock_fetcher.py  # 证券宝
│   ├── yfinance_fetcher.py  # Yahoo Finance
│   ├── longbridge_fetcher.py# 长桥 OpenAPI
│   ├── tickflow_fetcher.py  # TickFlow
│   └── fundamental_adapter.py # 基本面聚合
├── tests/                   # 单元测试
├── .env.example             # 环境变量模板
└── requirements.txt         # Python 依赖
```

## 支持的 LLM 提供商

| 提供商 | 环境变量 | 说明 |
|--------|----------|------|
| Anspire Open | `ANSPIRE_API_KEYS` | 一站式模型 + 搜索 |
| Gemini | `GEMINI_API_KEY` | 免费额度可用 |
| DeepSeek | `DEEPSEEK_API_KEY` | 性价比高 |
| AIHubmix | `AIHUBMIX_KEY` | 聚合多模型 |
| Anthropic Claude | `ANTHROPIC_API_KEY` | |
| OpenAI | `OPENAI_API_KEY` | |
| Ollama | `OLLAMA_API_BASE` | 本地部署，免费 |
| 通义千问 | DashScope 渠道 | |
| Kimi / Moonshot | Moonshot 渠道 | |
| 智谱 GLM | Zhipu 渠道 | |
| MiniMax | MiniMax 渠道 | |
| 硅基流动 | SiliconFlow 渠道 | |
| 火山方舟 / 豆包 | Volcengine 渠道 | |
| OpenRouter | OpenRouter 渠道 | |

支持多渠道配置（`LLM_CHANNELS`），自动轮询和 fallback。

## 交易理念

系统分析融入以下原则：

- **严进策略** — 不追高，乖离率 > 5% 不买入
- **趋势交易** — 只做 MA5 > MA10 > MA20 多头排列
- **效率优先** — 关注筹码集中度好的股票
- **买点偏好** — 缩量回踩 MA5/MA10 支撑

## License

MIT License - 详见 [LICENSE](LICENSE)
