# 单 Agent + ReAct 实现方案

> 目标：让已有的 22 个数据工具"串起来"，实现 **用户提问 → Agent 推理 → 调用工具 → 返回结论** 的完整闭环

---

## 一、现状盘点

### 已有

| 层 | 内容 | 位置 |
|---|---|---|
| 数据层（22 个工具） | 行情 6 + 基本面 6 + 新闻舆情 5 + 宏观 5 | `api/v1/endpoints/` (quotes, kline, market_status, sectors, stock_info, financials, macro) |
| LLM 调用 | LiteLLM Router，支持 Gemini/Claude/OpenAI/DeepSeek/Ollama | `src/analyzer.py` (GeminiAnalyzer) |
| 对话存储 | `ConversationMessage` 表 + save/get 方法 | `src/storage.py` |
| 工具 Schema 定义 | `ToolInfo`, `ToolParameterSchema` 等 Pydantic 模型 | `api/v1/schemas/tools.py` |
| 前端 SSE 推送 | 任务状态流式推送 | `src/services/task_queue.py` + 前端 `useTaskStream` |

### 缺失

| 缺什么 | 说明 |
|---|---|
| **ReAct 循环** | 当前是单次 LLM 调用，没有 Thought→Action→Observation 迭代 |
| **Tool 注册表** | 现有端点没有注册为 LLM 可调用的 function schema |
| **Agent 服务** | 没有编排层，没有 tool_call 解析和执行 |
| **Agent API** | 没有聊天式接口（现有 /analysis/analyze 是一次性提交） |
| **Chat UI** | 前端没有对话界面 |

---

## 二、架构设计

```
用户输入
   │
   ▼
┌─────────────────────────────────────────────────┐
│  Agent API  (POST /api/v1/agent/chat)            │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  StockAgent (ReAct Loop)                         │
│                                                  │
│  ┌───────────┐    ┌───────────┐    ┌──────────┐ │
│  │  Thought   │───▶│  Action   │───▶│Observation│ │
│  │  (LLM推理) │◀───│(工具调用)  │◀───│(工具结果) │ │
│  └───────────┘    └───────────┘    └──────────┘ │
│         │                              ▲         │
│         │  循环直到得出结论              │         │
│         ▼                              │         │
│  ┌───────────┐    ┌───────────────────────┐     │
│  │  Final     │    │   Tool Registry       │     │
│  │  Answer    │    │   (22+ 已注册工具)     │     │
│  └───────────┘    └───────────────────────┘     │
└─────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
  ConversationMessage    调用已有 API
  (SQLite 持久化)        (data_provider 层)
```

### 核心流程

```
1. 用户发送消息 → POST /api/v1/agent/chat
2. Agent 加载对话历史 + 系统提示词
3. LLM 决策：返回 tool_calls 或直接回复
4. 如果有 tool_calls → 执行工具 → 将结果加入上下文 → 回到步骤 3
5. 如果 LLM 直接回复 → 保存对话 → 返回给用户
6. 最大循环 10 次，防止无限循环
```

---

## 三、详细实现

### 3.1 Tool Registry — 把现有端点注册为 LLM 工具

**新建文件**: `src/agent/tool_registry.py`

核心职责：将每个数据函数包装成 LLM 可调用的 function schema + 执行器。

```python
# 工具定义示例（LiteLLM/OpenAI function calling 格式）
{
    "type": "function",
    "function": {
        "name": "get_realtime_quotes",
        "description": "获取单只或多只股票的实时报价，包括最新价、涨跌幅、成交量、成交额、换手率、市值等",
        "parameters": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "string",
                    "description": "股票代码，多个用逗号分隔，如 '600519,000001'"
                }
            },
            "required": ["symbols"]
        }
    }
}
```

**22 个工具注册清单**（直接映射已有端点）：

| # | 工具名 | 映射到 | 数据来源 |
|---|--------|--------|----------|
| 1 | `get_realtime_quotes` | `AkshareFetcher.get_realtime_quote()` | data_provider |
| 2 | `get_kline` | `AkshareFetcher.get_daily_data()` | data_provider |
| 3 | `get_history_data` | 同上（日期范围参数） | data_provider |
| 4 | `get_market_status` | `AkshareFetcher.get_market_stats()` | data_provider |
| 5 | `get_sector_list` | `AkshareFetcher.get_sector_rankings()` / `get_concept_rankings()` | data_provider |
| 6 | `get_stock_info` | `AkshareFetcher` + cninfo | api/v1/endpoints/stock_info.py |
| 7 | `get_financials` | `AkshareFundamentalAdapter` | api/v1/endpoints/financials.py |
| 8 | `get_balance_sheet` | 同上（报表类型=资产负债表） | api/v1/endpoints/financials.py |
| 9 | `get_income_statement` | 同上（报表类型=利润表） | api/v1/endpoints/financials.py |
| 10 | `get_cashflow` | 同上（报表类型=现金流量表） | api/v1/endpoints/financials.py |
| 11 | `get_valuation_ratios` | 同上（估值指标） | api/v1/endpoints/financials.py |
| 12 | `get_shareholder_structure` | 同上（股东结构） | api/v1/endpoints/financials.py |
| 13 | `search_news` | `SearchService` + `AkshareFetcher` | api/v1/endpoints/financials.py |
| 14 | `get_sentiment` | 股吧 NLP 分析 | api/v1/endpoints/financials.py |
| 15 | `get_announcements` | 公告数据 | api/v1/endpoints/financials.py |
| 16 | `get_research_report` | 研报数据 | api/v1/endpoints/financials.py |
| 17 | `get_social_sentiment` | 股吧热度 | api/v1/endpoints/financials.py |
| 18 | `get_index_data` | 新浪指数日线 | api/v1/endpoints/macro.py |
| 19 | `get_bond_yield` | 东方财富债券收益率 | api/v1/endpoints/macro.py |
| 20 | `get_macro_indicator` | PMI/CPI/PPI 等 | api/v1/endpoints/macro.py |
| 21 | `get_sector_flow` | 板块资金流向 | api/v1/endpoints/macro.py |
| 22 | `get_market_breadth` | 市场宽度 | api/v1/endpoints/macro.py |

**实现策略**：每个工具的执行函数直接调用底层 `data_provider` 或 `storage` 方法，不走 HTTP 绕路。

### 3.2 StockAgent — ReAct 循环引擎

**新建文件**: `src/agent/stock_agent.py`

```python
class StockAgent:
    """单 Agent ReAct 循环"""

    MAX_ITERATIONS = 10  # 最大推理轮次

    def __init__(self, config: Config):
        self.config = config
        self.tool_registry = ToolRegistry()

    async def run(self, user_message: str, session_id: str) -> AsyncGenerator[str, None]:
        """
        ReAct 主循环，yield SSE 事件流

        事件类型:
          - thought:   Agent 思考过程
          - tool_call: 调用了什么工具
          - tool_result: 工具返回结果
          - answer:    最终回答
          - error:     错误
        """
        # 1. 加载对话历史
        history = self._load_history(session_id)

        # 2. 构建消息列表
        messages = self._build_messages(history, user_message)

        # 3. ReAct 循环
        for i in range(self.MAX_ITERATIONS):
            # 3a. 调用 LLM（带 tools 参数）
            response = await self._call_llm(messages)

            # 3b. 判断：有 tool_calls 还是直接回复？
            if response has tool_calls:
                # 执行工具 → 结果加入 messages → 继续循环
                for tool_call in response.tool_calls:
                    yield {"type": "tool_call", ...}
                    result = self.tool_registry.execute(tool_call)
                    yield {"type": "tool_result", ...}
                    messages.append(tool_result_message)
            else:
                # LLM 直接回复 → 结束循环
                yield {"type": "answer", "content": response.content}
                break

        # 4. 保存对话到 ConversationMessage
        self._save_conversation(session_id, user_message, final_answer)
```

**关键决策**：

| 决策点 | 选择 | 原因 |
|--------|------|------|
| LLM 调用方式 | LiteLLM `completion(tools=...)` | 项目已用 LiteLLM，原生支持 function calling |
| 流式输出 | SSE 事件流 | 前端已有 SSE 基础设施，零额外成本 |
| 工具执行 | 直接调 Python 函数 | 不走 HTTP 绕路，低延迟 |
| 对话持久化 | 复用 `ConversationMessage` | 表和 CRUD 方法已存在 |
| 并发模型 | asyncio | FastAPI 原生异步，工具调用多数是 I/O 密集 |

### 3.3 Agent API 端点

**新建文件**: `api/v1/endpoints/agent.py`

```python
router = APIRouter(prefix="/agent", tags=["Agent"])

@router.post("/chat")
async def agent_chat(request: AgentChatRequest):
    """
    Agent 对话接口

    请求体:
      - message: str       用户消息
      - session_id: str    会话 ID（可选，不传则新建）
      - stream: bool       是否流式返回（默认 true）

    返回:
      - 非流式: JSON { session_id, answer, tool_calls_trace }
      - 流式: SSE 事件流
    """

@router.get("/sessions")
async def list_sessions():
    """列出所有 Agent 会话"""

@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取某个会话的完整对话历史"""

@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
```

**注册到路由**: 在 `api/v1/router.py` 中添加 agent 路由。

### 3.4 系统提示词

Agent 的系统提示词是效果的关键。结构如下：

```markdown
你是 A 股智能分析助手，擅长股票分析、行业研究和投资辅助。

## 核心原则
1. 所有数据必须通过工具获取，不得编造任何数字
2. 分析要有理有据，每个结论都要有数据支撑
3. 风险提示优先，宁可不推荐也不做错误推荐
4. 回答使用中文

## 可用工具
{tool_descriptions}

## 分析框架
- 基本面分析：财务指标 → 估值水平 → 股东结构
- 技术面分析：K线形态 → 量价关系 → 资金流向
- 消息面分析：新闻舆情 → 公告研报 → 社交情绪
- 宏观面分析：大盘指数 → 宏观指标 → 板块轮动

## 工作方式
1. 先理解用户意图，判断需要哪些数据
2. 调用工具获取数据，不要一次调太多
3. 分析数据，形成判断
4. 如果信息不足，继续调用工具补充
5. 给出结构化的分析结论

## 输出格式
{根据用户问题的复杂度，选择简洁或详细格式}
```

### 3.5 前端 Chat UI（最小可用版）

**新建文件**:

```
apps/dsa-web/src/
  pages/AgentChatPage.tsx         — 聊天页面
  components/agent/
    ChatMessageList.tsx           — 消息列表
    ChatInput.tsx                 — 输入框
    ToolCallTrace.tsx             — 工具调用轨迹展示
  api/agent.ts                    — Agent API 客户端
  types/agent.ts                  — Agent 相关类型
  hooks/useAgentChat.ts           — Agent 聊天 hook
```

**路由**: 在 `App.tsx` 添加 `/agent` 路由。

**最小 UI**：
- 顶部：会话标题 + 新建会话按钮
- 中部：消息列表（用户消息 + AI 回复 + 工具调用气泡）
- 底部：输入框 + 发送按钮
- 工具调用展示：折叠式，显示工具名 + 参数 + 结果摘要

---

## 四、文件变更清单

### 新建文件（后端）

| 文件 | 用途 | 行数估算 |
|------|------|----------|
| `src/agent/__init__.py` | 包初始化 | 5 |
| `src/agent/tool_registry.py` | 工具注册表（22 个工具定义 + 执行器） | ~400 |
| `src/agent/stock_agent.py` | ReAct 循环引擎 | ~250 |
| `src/agent/prompts.py` | 系统提示词模板 | ~80 |
| `api/v1/endpoints/agent.py` | Agent API 端点 | ~200 |
| `api/v1/schemas/agent.py` | Agent 请求/响应模型 | ~60 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `api/v1/router.py` | 注册 agent 路由 |
| `requirements.txt` | 无需新增（LiteLLM 已支持 function calling） |

### 新建文件（前端）

| 文件 | 用途 | 行数估算 |
|------|------|----------|
| `apps/dsa-web/src/pages/AgentChatPage.tsx` | 聊天页面 | ~150 |
| `apps/dsa-web/src/components/agent/ChatMessageList.tsx` | 消息列表 | ~120 |
| `apps/dsa-web/src/components/agent/ChatInput.tsx` | 输入框 | ~60 |
| `apps/dsa-web/src/components/agent/ToolCallTrace.tsx` | 工具调用展示 | ~80 |
| `apps/dsa-web/src/api/agent.ts` | Agent API | ~60 |
| `apps/dsa-web/src/types/agent.ts` | 类型定义 | ~50 |
| `apps/dsa-web/src/hooks/useAgentChat.ts` | 聊天 hook | ~120 |

### 修改文件（前端）

| 文件 | 改动 |
|------|------|
| `apps/dsa-web/src/App.tsx` | 添加 `/agent` 路由 |
| `apps/dsa-web/src/components/layout/Shell.tsx` | 侧边栏添加 Agent 入口 |

---

## 五、实现顺序

### Step 1: Tool Registry（核心基础）

1. 创建 `src/agent/__init__.py`
2. 创建 `src/agent/tool_registry.py`
   - 定义 `ToolDef` 数据类（name, description, parameters, executor）
   - 实现 `ToolRegistry` 类：注册、查询、执行
   - 逐个注册 22 个工具，每个工具的 executor 直接调用 data_provider 层
3. 编写单元测试验证每个工具定义格式正确

**验收标准**：`ToolRegistry.get_all_schemas()` 返回 22 个合法的 function calling schema

### Step 2: StockAgent ReAct 循环

1. 创建 `src/agent/stock_agent.py`
   - 实现 `run()` 方法：ReAct 循环
   - 集成 LiteLLM `completion(tools=...)` 调用
   - 处理 tool_calls 响应 → 执行工具 → 回注结果
   - 最大迭代次数保护
   - 对话历史管理
2. 创建 `src/agent/prompts.py`
   - 系统提示词模板
   - 工具描述动态注入
3. 集成 ConversationMessage 持久化

**验收标准**：能用 Python 直接调用 `StockAgent.run("分析贵州茅台")`，Agent 自动调用 get_realtime_quotes → get_financials → 返回分析结论

### Step 3: Agent API

1. 创建 `api/v1/schemas/agent.py` — 请求/响应模型
2. 创建 `api/v1/endpoints/agent.py` — 4 个端点
3. 修改 `api/v1/router.py` — 注册路由

**验收标准**：`curl -X POST /api/v1/agent/chat -d '{"message":"分析600519"}'` 返回正确结果

### Step 4: 前端 Chat UI

1. 创建 `types/agent.ts` — 类型定义
2. 创建 `api/agent.ts` — API 客户端
3. 创建 `hooks/useAgentChat.ts` — 聊天状态管理
4. 创建 `components/agent/` — ChatMessageList, ChatInput, ToolCallTrace
5. 创建 `pages/AgentChatPage.tsx` — 聊天页面
6. 修改 `App.tsx` + `Shell.tsx` — 路由和导航

**验收标准**：在浏览器打开 `/agent`，输入问题，看到 Agent 调用工具并返回分析结果

---

## 六、关键技术细节

### 6.1 LiteLLM Function Calling

LiteLLM 原生支持 OpenAI 格式的 function calling：

```python
import litellm

response = litellm.completion(
    model="gemini/gemini-2.5-flash",
    messages=messages,
    tools=tool_registry.get_all_schemas(),  # OpenAI function format
    tool_choice="auto",
)

# 处理 tool_calls
if response.choices[0].message.tool_calls:
    for tool_call in response.choices[0].message.tool_calls:
        function_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)
        result = tool_registry.execute(function_name, arguments)
        # 回注结果到 messages
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result, ensure_ascii=False)
        })
```

### 6.2 工具结果格式化

工具返回的原始数据可能很大（如完整 K 线），需要截断：

```python
def format_tool_result(name: str, result: Any, max_chars: int = 4000) -> str:
    """格式化工具结果，避免超出上下文窗口"""
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        # 保留结构，截断数据
        text = text[:max_chars] + "\n...[数据已截断]"
    return text
```

### 6.3 SSE 事件流格式

```
event: thought
data: {"content": "用户想分析茅台，我需要先获取实时行情..."}

event: tool_call
data: {"tool": "get_realtime_quotes", "args": {"symbols": "600519"}}

event: tool_result
data: {"tool": "get_realtime_quotes", "result_summary": "600519 贵州茅台 最新价 1680.00 涨跌幅 +1.2%"}

event: tool_call
data: {"tool": "get_financials", "args": {"symbol": "600519"}}

event: tool_result
data: {"tool": "get_financials", "result_summary": "ROE 30.2% 毛利率 91.5% 净利率 49.8%"}

event: answer
data: {"content": "## 贵州茅台(600519) 分析\n\n..."}
```

### 6.4 模型要求

Function calling 不是所有模型都支持。最低要求：

| 模型 | 支持 function calling |
|------|----------------------|
| Gemini 2.5 Flash/Pro | ✅ |
| Claude Sonnet/Opus | ✅ |
| GPT-4o / GPT-4.1 | ✅ |
| DeepSeek V3 | ✅ |
| Ollama (Qwen3 等) | ⚠️ 部分模型支持 |

需要在 Agent 启动时检查当前模型是否支持 tools，不支持则降级为纯文本模式（不调工具，直接让 LLM 回复）。

---

## 七、风险与边界

1. **Token 消耗**：ReAct 循环每次带完整工具列表 + 历史上下文，token 消耗是单次调用的 3-5 倍。需要限制最大迭代次数和上下文长度
2. **工具调用延迟**：akshare 部分接口响应慢（2-5 秒），多轮工具调用会导致总延迟较高。流式输出可缓解用户等待感
3. **幻觉风险**：即使走工具调用，LLM 仍可能曲解数据。工具结果必须原样注入，LLM 只做推理
4. **模型兼容性**：部分 Ollama 本地模型不支持 function calling，需要降级方案
5. **Agent 定位**：分析辅助，不是投资决策者。所有输出必须附带"仅供参考，不构成投资建议"

---

## 八、不在本次范围

- 多 Agent 编排（Orchestrator → Screener → Analyst 拆分，v0.2 再做）
- 分析层工具（筛选器、技术指标、估值模型、财务质量评分，v0.2 再做）
- 风控层工具（仓位计算、持仓分析，v0.3 再做）
- 记忆机制（WorkingMemory / EpisodicMemory / SemanticMemory，v0.3 再做）
- Reflection 自检，v1.0 再做
