# 外部环境分析 Card 设计

## 概述

在个股分析页 > 业务分析 tab 中，"业务动向分析"卡片下方新增"外部环境分析"卡片。分析个股所处的结构性外部环境，覆盖政策、技术、需求、供给与竞争 4 个维度，帮助投资者判断公司面临的宏观驱动力。

## 设计原则

- **聚焦结构性因素**：政策变化、技术变革、需求趋势、供给格局，不涉及盘面情绪数据（涨跌幅、资金流向等）
- **数据源复用优先**：新闻/公告/主营构成从现有 business 数据中获取，仅宏观指标需要新增拉取
- **交互一致性**：与"业务动向分析"卡片保持完全一致的视觉风格和交互模式

## 数据源

| 数据 | 来源 | 获取方式 | 用途 |
|------|------|----------|------|
| 近期新闻 (10条) | 已有 business.events.news | 复用，不重复拉取 | LLM 语义提取外部信号 |
| 近期公告 (15条) | 已有 business.events.announcements | 复用，不重复拉取 | LLM 语义提取政策/监管信号 |
| 主营构成 + 业务介绍 | 已有 business.intro + business.composition | 复用 | 帮 LLM 定位公司行业 |
| PMI | akshare `macro_china_pmi()` | 新增拉取 | 宏观经济景气度 |
| CPI | akshare `macro_china_cpi()` | 新增拉取 | 通胀/需求温度 |
| PPI | akshare `macro_china_ppi()` | 新增拉取 | 工业品价格/供给压力 |

## 架构：扩展当前 SSE 流

### 执行流程

```
[现有] 数据拉取(5线程并行)
  ↓
[现有] 业务动向分析 LLM call → analysis_chunk 流式推送 → analysis_done
  ↓
[新增] 宏观数据拉取 (PMI/CPI/PPI, 单线程，数据量小)
  ↓
[新增] 外部环境分析 LLM call → env_analysis_chunk 流式推送 → env_analysis_done
```

### SSE 协议扩展

新增 3 个事件类型：

| 事件名 | 触发时机 | data 内容 |
|--------|----------|-----------|
| `env_analysis_start` | 宏观数据就绪，LLM 开始分析 | `{}` |
| `env_analysis_chunk` | LLM 流式输出每个 token | `{ "text": "..." }` |
| `env_analysis_done` | 完整结果 | `EnvironmentAnalysis` JSON |

前端在 `useBusinessStream` hook 中新增对这 3 个事件的监听，用独立的 `envStreamingText` 状态管理流式文本。

### 容错

- 环境分析失败（LLM 不可用、宏观数据拉取失败）不影响"业务动向分析"的正常展示
- `analysis_done` 事件仍然正常发出，`business` 对象中的 `environment_analysis` 字段为 null 或含 error 信息
- 前端：`environment_analysis` 为空时不渲染环境分析卡片

### 缓存

- 环境分析结果包含在现有 business 缓存中（key: `stock_business:v2:{symbol}:{YYYYMMDD}`）
- 缓存命中时：如果缓存包含环境分析结果，通过 `env_analysis_done` 事件直接推送；否则跳过
- 缓存版本升级：v2 → v3（因为新增字段，旧缓存不含环境分析）

## LLM 输出格式

后端要求 LLM 输出结构化 JSON，前端直接解析渲染：

```typescript
interface EnvironmentDimension {
  signal: "利好" | "利空" | "中性";
  summary: string;      // 2-3 句话分析
  factors: string[];    // 关键信号标签，如 ["补贴延续", "碳中和目标"]
}

interface EnvironmentAnalysis {
  policy: EnvironmentDimension;           // 政策环境
  technology: EnvironmentDimension;       // 技术变革
  demand: EnvironmentDimension;           // 需求变化
  supply_competition: EnvironmentDimension; // 供给与竞争
  macro_context: string;                  // 宏观背景总结（1-2句话）
  llm_used: boolean;
  model?: string;
  llm_input?: string;                     // 完整 prompt，可折叠展示
  error?: string;
}
```

在 `BusinessResponse` 中新增字段：

```typescript
interface BusinessResponse {
  // ... 现有字段不变
  environment_analysis?: EnvironmentAnalysis;  // 新增
}
```

## LLM Prompt 设计

### System Prompt

```
你是一个资深A股行业分析师，擅长从宏观环境和外部因素中判断对公司业务的影响。
请用中文回答，输出严格的 JSON 格式（不要 markdown 代码块包裹）。
分析要简洁有力，每个维度不超过3句话。
```

### User Prompt 结构

```
请分析 {symbol}（{company_name}）所处的外部环境。

【公司所处行业】
- 主营业务：{main_business}
- 产品类型：{product_type}

【近期行业新闻/公告摘要】
{从已有 news + announcements 中筛选与行业相关的条目}

【宏观经济数据】
- PMI（近3个月）：{pmi_values}
- CPI（近3个月）：{cpi_values}
- PPI（近3个月）：{ppi_values}

请按以下 JSON 格式输出：
{
  "policy": { "signal": "利好/利空/中性", "summary": "...", "factors": [...] },
  "technology": { ... },
  "demand": { ... },
  "supply_competition": { ... },
  "macro_context": "..."
}
```

### 输出处理

LLM 输出 JSON 文本，后端用 `json.loads()` 解析为 dict，存入 `environment_analysis` 字段。如果 JSON 解析失败，将整个文本作为 `summary` 字段降级处理，前端直接显示。

## 前端 Card 设计

### 组件：`EnvironmentAnalysisCard`

新建独立组件，放在 `apps/dsa-web/src/pages/StockAnalysisPage.tsx` 中（与 `BusinessAnalysisPanel` 同级），如果代码量较大则提取到 `components/` 目录。

### 视觉结构

```
┌─────────────────────────────────────────────┐
│ 🌐 外部环境分析                    gpt-4o   │
├─────────────────────────────────────────────┤
│ 宏观背景                                     │
│ PMI 连续3月扩张，CPI温和，宏观环境偏暖         │
├─────────────────────────────────────────────┤
│ 🏛 政策环境        [利好]  (绿色badge)        │
│ 国务院发布新能源补贴延续政策，公司光伏业务...   │
│ #补贴延续  #碳中和目标                        │
├─────────────────────────────────────────────┤
│ 🔬 技术变革        [中性]  (灰色badge)        │
│ 行业技术路线稳定，暂无重大变革信号              │
├─────────────────────────────────────────────┤
│ 📈 需求变化        [利空]  (红色badge)        │
│ 地产下游需求持续走弱，新开工面积连续下降        │
│ #地产下行  #新开工萎缩                        │
├─────────────────────────────────────────────┤
│ 🏭 供给与竞争      [利好]  (绿色badge)        │
│ 行业供给侧出清，龙头集中度提升                 │
│ #产能出清  #集中度提升                        │
├─────────────────────────────────────────────┤
│ ▶ 分析输入数据                               │
└─────────────────────────────────────────────┘
```

### 交互规范

- 使用 `stock-analysis-panel` 样式类，与"业务动向分析"完全一致
- 标题栏：`Globe` icon + "外部环境分析" + 右侧 model name
- signal badge 颜色映射：利好 = `bg-emerald-50 text-emerald-700`，利空 = `bg-red-50 text-red-700`，中性 = `bg-slate-100 text-slate-500`
- factors 以小标签形式展示，样式 `bg-slate-50 text-slate-500 text-xs rounded px-1.5 py-0.5`
- "分析输入数据" 折叠区域：与业务动向分析一致的 `ChevronRight` 按钮 + `pre` 代码块
- 流式阶段：`env_analysis_chunk` 到达时实时渲染 Markdown（LLM 可能输出非 JSON 的降级文本）

### SSE 流式渲染

流式阶段（`env_analysis_chunk` 累积中）：
- 显示 `LoaderCircle` + "AI 正在分析外部环境..."
- 如果有累积的 `envStreamingText`，用 `ReactMarkdown` 实时渲染
- `env_analysis_done` 到达后切换为结构化 Card 渲染

## 涉及文件变更

| 文件 | 变更内容 |
|------|----------|
| `api/v1/endpoints/stock_info.py` | 新增 `_fetch_macro_data()`、`_build_environment_prompt()`、`_parse_environment_analysis()`；修改 SSE stream 和同步端点加入环境分析 |
| `apps/dsa-web/src/api/business.ts` | 新增 `EnvironmentDimension`、`EnvironmentAnalysis` 类型；`BusinessResponse` 新增 `environment_analysis` 字段 |
| `apps/dsa-web/src/hooks/useBusinessStream.ts` | 新增 `envStreamingText` 状态 + `env_analysis_*` 事件监听 |
| `apps/dsa-web/src/pages/StockAnalysisPage.tsx` | 新增 `EnvironmentAnalysisCard` 组件；`BusinessAnalysisPanel` 中渲染新卡片 |

## 边界情况

- **LLM 不可用**：`environment_analysis.llm_used = false`，前端不渲染卡片
- **宏观数据拉取失败**：prompt 中标注"宏观数据暂不可用"，LLM 仍基于新闻/公告分析
- **LLM 输出非 JSON**：降级为纯文本展示，前端用 `ReactMarkdown` 渲染 `raw_text` 字段
- **缓存命中但无环境分析**（v2 旧缓存）：跳过环境分析卡片展示，不报错
