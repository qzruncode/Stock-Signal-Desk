# Agent Tool Audit

本文档用于约束 `/api/v1/agent/chat` 暴露给模型的 22 个工具，目标是：

- 让模型更稳定地拿到“足够回答问题”的数据
- 减少把前端展示型原始数据直接塞进上下文
- 降低重复调用、长窗口调用和无效调用带来的 token 浪费

补充说明：当前 agent 已在原有数据工具之外新增联网兜底搜索工具，因此实际可用工具数已超过最初的 22 个。

## 当前策略

模型侧工具调用分两层约束：

1. `src/agent/tool_registry.py`
   - 控制工具 schema、默认参数、名称解析
   - 让模型更容易选到合适工具
2. `api/v1/endpoints/agent.py`
   - 控制工具结果进入 LLM 前的压缩和裁剪
   - 让模型拿到摘要化、低噪音的数据

## 已落地优化

### 1. 工具结果压缩

在 `api/v1/endpoints/agent.py::_compact_tool_result()` 中已做：

- `get_kline` / `get_history_data`
  - 保留 `latest`、`recent`、`range`
  - 不再把长历史序列原样送入上下文
- `get_sector_list`
  - 改为 `top_movers` / `bottom_movers`
  - 不再把全部行业/概念板块一次性送给模型
- `search_news` / `get_sentiment` / `get_social_sentiment` / `get_research_report`
  - 保留聚合指标、结构化 `analysis`、少量代表性 `items`
  - 限制原始条目数量
- `get_sector_flow`
  - 仅保留 `inflow_top` / `outflow_top`
  - 移除重复意义的 `records` 大列表
- 财务类工具
  - 聚焦 `latest` 和 `recent_periods`
  - 不再默认给全量长序列

### 2. 名称解析

在 `src/agent/tool_registry.py` 中已做：

- 工具 executor 调用前，先尝试通过 `resolve_name_to_code()` 解析股票名称
- 覆盖：
  - `get_realtime_quotes`
  - `get_kline`
  - `get_history_data`
  - `get_stock_info`
  - 财务类工具
  - 新闻/公告/舆情/研报工具

效果：

- 用户输入“贵州茅台”“宁德时代”时，模型可以直接调用工具
- 避免 prompt 让模型“先确认代码”，但 endpoint 实际只认代码造成的失配

### 4. 联网兜底

已新增显式兜底工具：

- `search_web_news`
- `search_web_price_fallback`
- `fetch_web_content`

用途：

- 当结构化新闻工具失败、空数据或明显过时时，模型可显式调用 `search_web_news`
- 当行情/K线工具失败、空数据或明显过时时，模型可显式调用 `search_web_price_fallback`
- 当需要阅读单条外部页面正文时，可调用 `fetch_web_content`

同时，agent 执行层还增加了自动兜底增强：

- `get_realtime_quotes` / `get_kline` / `get_history_data`
  - 空数据或明显过时，会自动附带 `search_fallback.type=price`
- `search_news` / `get_announcements` / `get_sentiment` / `get_research_report` / `get_social_sentiment`
  - 空数据或明显过时，会自动附带 `search_fallback.type=news`

这样即使模型没有主动想到“再搜一下”，上下文里也会带上一层联网兜底结果。

### 5. 标准新鲜度字段

关键工具正在逐步统一输出以下字段：

- `data_time`
  - 当前结果中最关键数据的时间
- `is_stale`
  - 当前结果是否明显过时
- `fallback_used`
  - 当前结果是否已使用数据源降级、缓存兜底或其他后备路径

当前已补齐的重点工具：

- `get_realtime_quotes`
- `get_kline`
- `get_history_data`
- `get_market_status`
- `get_sector_list`
- `search_news`
- `get_announcements`
- `get_sentiment`
- `get_research_report`
- `get_social_sentiment`
- `get_index_data`
- `get_bond_yield`
- `get_macro_indicator`
- `get_sector_flow`
- `get_market_breadth`

agent 层会优先读取这些标准字段，而不是完全依赖启发式判断日期。

### 3. 默认窗口收紧

在 `src/agent/tool_registry.py` 中已收紧默认参数：

- `get_kline`: `120 -> 60`
- `get_financials`: `12 -> 6`
- `get_balance_sheet` / `get_income_statement` / `get_cashflow`: `12 -> 4`
- `search_news` / `get_sentiment` / `get_announcements` / `get_social_sentiment`: `90 -> 30`
- `get_research_report`: `1095 -> 365`

## 22 个工具推荐使用方式

### 行情与技术面

#### `get_realtime_quotes`

适用：
- 最新价格、涨跌幅、量额、估值快照

推荐：
- 先用它判断是否需要继续看 K 线
- 多标的对比时优先调用它，不要先拉多只股票的长 K 线

#### `get_kline`

适用：
- 看趋势、支撑压力、量价关系

推荐：
- 默认 `count=60`
- 只有明确做中长期复盘时再扩大窗口

#### `get_history_data`

适用：
- 用户明确指定某一时间段

推荐：
- 只在用户给出起止区间、事件复盘、财报前后走势对比时使用

### 市场与板块

#### `get_market_status`

适用：
- 先判断今天市场环境、风险偏好、赚钱效应

推荐：
- 做单股分析前，如果用户问的是“今天能不能做”或“当前市场环境”，优先调用

#### `get_sector_list`

适用：
- 看行业/概念整体强弱

推荐：
- 用于判断股票所属板块是否顺风
- 不要和 `get_sector_flow` 重复调用，除非既要看涨跌幅也要看资金流

#### `get_sector_flow`

适用：
- 看主力资金轮动

推荐：
- 适合回答“最近资金在往哪流”“哪些方向更强”

#### `get_market_breadth`

适用：
- 看赚钱效应、涨跌比、涨停跌停、炸板率

推荐：
- 回答短线情绪问题时优先于大量个股微观查询

### 个股资料与财务

#### `get_stock_info`

适用：
- 名称确认、行业确认、基本画像

推荐：
- 用户输入股票名称时优先使用
- 但现在工具层已支持名称解析，不再要求每次都机械先查一次

#### `get_financials`

适用：
- 快速看盈利能力、成长性、偿债能力

推荐：
- 财务分析优先用这个总览工具
- 只有需要深挖结构时再调用三大报表

#### `get_balance_sheet` / `get_income_statement` / `get_cashflow`

适用：
- 深挖资产结构、利润结构、现金流质量

推荐：
- 默认最近 4 个报告期
- 不要三张报表无差别一起拉，按问题取用

#### `get_valuation_ratios`

适用：
- 估值高低、历史分位、行业对比

推荐：
- 回答“贵不贵”“当前估值处于什么位置”时优先

#### `get_shareholder_structure`

适用：
- 股东人数变化、前十大股东、重要增减持

推荐：
- 适合问筹码稳定性、机构持仓、控制权变化

### 新闻、公告、舆情

#### `search_news`

适用：
- 看近况、事件驱动、催化与风险点

推荐：
- 默认近 30 天
- 优先读 `analysis` 和少量关键事件，不要依赖全量原始条目

#### `get_announcements`

适用：
- 正式披露事项、业绩、分红、增减持、高管变动

推荐：
- 要求高可信度事件时优先于泛新闻

#### `get_sentiment`

适用：
- 聚合财经资讯后的整体舆情倾向

推荐：
- 用于辅助，不应单独作为交易结论依据

#### `get_research_report`

适用：
- 卖方评级、盈利预测、机构覆盖情况

推荐：
- 默认近 365 天足够
- 用户只问短期事件时，不要优先调用

#### `get_social_sentiment`

适用：
- 股吧讨论热度、阅读评论量、讨论倾向

推荐：
- 适合回答短线情绪和讨论热度问题
- 噪音较大，应作为辅助信号

### 宏观

#### `get_index_data`

适用：
- 大盘指数走势

推荐：
- 回答“指数环境”“市场趋势”时优先

#### `get_bond_yield`

适用：
- 利率环境、期限利差

推荐：
- 只有在宏观/风格问题中再调用，不适合大多数个股问答默认触发

#### `get_macro_indicator`

适用：
- PMI、CPI、PPI、GDP、M2、社融、LPR

推荐：
- 只有用户明确问宏观，或结论确实依赖宏观时再调用

## 仍需注意的点

### 1. endpoint 仍然是前端优先设计

虽然 agent 层已做压缩，但原始 endpoint 仍然保留大量展示字段。后续如果要进一步优化，可以考虑：

- 给 agent 单独提供更轻量的内部 service 层
- 避免先构造大 payload 再裁剪

### 2. 新闻类工具仍可能有时间跨度误用

虽然 registry 默认窗口已收紧，但模型仍可能显式传大窗口。后续可考虑：

- 在 agent 层增加参数上限重写策略
- 或在 system prompt 中继续强化“默认近期优先”

### 3. 宏观工具对个股问答仍可能被过度调用

后续可以继续观察真实对话日志，如果宏观调用频率过高，可再加一层策略：

- 个股问答默认不主动拉宏观
- 只有用户显式问“大盘/宏观/风格/利率”再触发

## 验证

已添加测试：

- `tests/test_agent_tool_result_compaction.py`
- `tests/test_tool_registry_model_fitness.py`

建议回归命令：

```bash
python3 -m unittest \
  tests.test_tool_registry_model_fitness \
  tests.test_agent_tool_result_compaction -v
```
