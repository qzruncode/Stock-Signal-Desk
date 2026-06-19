# 买入分析页面重构设计

> 日期: 2026-06-19
> 状态: 设计完成，待实现

## 1. 背景与目标

### 现状

当前"买入判断"页面（`BuyDecisionWorkbench`）采用 6 步串行流程（个股基础 → 行业β → 主线属性 → 受益弹性 → 风险约束 → 最终决策），存在以下问题：

- 代码已标记 `TODO: 待重做`，不应再扩展
- 前端 `BuyDecisionWorkbench.tsx` 927 行、`useBuyDecisionWorkbench.ts` 718 行、`StockAnalysisPage.tsx` 1672 行，单体结构难以维护
- 后端 `buy_decision_workbench_service.py` 1236 行，无 Pydantic model，直接调用 endpoint 函数，无持久化
- 最终决策是硬编码规则引擎，缺乏 LLM 定性分析能力
- 进度模拟（占位文本每 900ms 追加）与实际后端进度无关，误导用户

### 目标

重构为基于 **8 项准则全部满足** 的判定模式：

```
1. 当前属于市场主线 / 分支主线
2. 行业处于上升周期（景气上行）
3. 行业未来3年空间明确（非存量博弈）
4. 不属于价格战/内卷行业
5. 有政策 / 技术 / 需求驱动
6. 未来 6-12 个月仍有催化
7. 估值未透支
8. 没有未暴雷致命风险
```

8 项全过 = 可买入，任意 1 项不过 = 不可买入。

## 2. 核心设计决策

| 决策项 | 选择 | 理由 |
|---|---|---|
| 判定方式 | 数据 + LLM 混合 | 每个准则展示数据证据，LLM 综合给出通过/不通过的定性判断 |
| 页面位置 | 保持为 StockAnalysisPage 的 tab | 与其他分析 tab 并列，上下文共享 |
| 展示结构 | 8 项平铺 | 每项一张卡片，从上到下排列，简单直观 |
| 执行流程 | 顺序执行，可提前终止 | 节省 LLM 开销，第 1 项不过就不继续 |
| 最终结论 | 全过才可买，否则不过 | 严格逻辑，结论清晰 |
| 卡片内容 | LLM 结论 + 可折叠底层数据 | 主阅读流简洁，高级用户可展开验证 |
| 架构模式 | 独立评估器 | 每个准则独立可测，模块化和可维护性最高 |

## 3. 架构

### 3.1 整体数据流

```
用户选择股票 → 点击"开始分析"
        ↓
  CriterionOrchestrator (后端)
        ↓ 顺序调用 8 个评估器
  每个评估器: collect_data() → LLM分析 → 返回 {pass, verdict, evidence}
        ↓ 每完成一个立即通过 SSE 推送
  useBuyCriteria hook (前端)
        ↓ 驱动渲染
  8 张 CriterionCard 依次从"等待"变为"分析中"变为"完成"
        ↓
  任意 fail → 停止后续评估
  8/8 全过 → 显示"可买入"结论
```

### 3.2 后端架构

```
api/v1/endpoints/buy_decision.py          # 路由层
  └── POST /criteria/analyze              # 启动分析，返回 SSE 流
  └── GET  /criteria/{task_id}/status     # 查询状态

src/services/buy_criteria/
  ├── __init__.py
  ├── orchestrator.py                     # CriterionOrchestrator - 顺序调度
  ├── base.py                             # BaseCriterionEvaluator 抽象基类
  ├── evaluators/
  │   ├── mainline_position.py            # ① 市场主线属性
  │   ├── prosperity_cycle.py             # ② 景气上行周期
  │   ├── growth_space.py                 # ③ 未来3年空间
  │   ├── competition_landscape.py        # ④ 竞争格局
  │   ├── growth_drivers.py               # ⑤ 驱动因素
  │   ├── catalyst_events.py              # ⑥ 催化事件
  │   ├── valuation_level.py              # ⑦ 估值水位
  │   └── fatal_risks.py                  # ⑧ 致命风险
  └── data_service.py                     # DataService - 统一数据拉取封装

src/services/buy_criteria/prompts/
  └── criterion_rubrics.py                # 每个准则的判定标准文本
```

### 3.3 前端架构

```
apps/dsa-web/src/
  ├── components/buyCriteria/
  │   ├── BuyCriteriaPanel.tsx            # Tab 根组件（容器）
  │   ├── SummaryBar.tsx                  # 顶部总结栏
  │   └── CriterionCard.tsx               # 单张准则卡片
  ├── hooks/
  │   └── useBuyCriteria.ts              # SSE 订阅 + 状态管理
  └── api/
      └── buyCriteria.ts                 # HTTP 客户端 + 类型定义
```

## 4. 评估器详细设计

### 4.1 统一返回结构

```typescript
interface CriterionResult {
  criterion_id: string;        // e.g. "mainline_position"
  criterion_name: string;      // e.g. "市场主线属性"
  index: number;               // 0-7
  pass: boolean;
  verdict: string;             // LLM 2-3 句话定性判断
  evidence: {
    raw_data: Record<string, any>;   // 采集的原始数据
    data_summary: string;            // 数据摘要（给人看的）
  };
  analyzed_at: string;         // ISO timestamp
}
```

### 4.2 八项准则

| # | criterion_id | 准则名 | 数据源 | LLM 判定职责 |
|---|---|---|---|---|
| 1 | `mainline_position` | 市场主线属性 | 行业资金流向、板块热度/成交额占比、政策提及频率、行业 sentiment | 综合资金、政策、市场共识判断是否为市场主线/分支主线 |
| 2 | `prosperity_cycle` | 景气上行周期 | 行业景气度评分、营收/利润增速趋势（近4季度）、PMI 细分指标、产能利用率 | 判断行业处于上行期/顶部/下行期/底部复苏，只接受"上行期"和"底部复苏"为通过 |
| 3 | `growth_space` | 未来3年空间 | 行业市场规模预测、渗透率数据、CAGR 预测、新增需求来源 | 判断增长是结构性增量还是存量份额争夺，存量博弈 = 不过 |
| 4 | `competition_landscape` | 竞争格局 | 行业集中度 CR5/HHI、毛利率趋势、头部企业毛利率差异、价格战信号 | 毛利率稳定或提升 + 集中度合理 = 通过；毛利率持续下降 + 价格战 = 不过 |
| 5 | `growth_drivers` | 驱动因素 | 近期政策文件/会议、技术突破/专利趋势、下游需求数据/订单增长、资本开支方向 | 识别核心驱动力（政策/技术/需求），评估可持续性，纯概念炒作 = 不过 |
| 6 | `catalyst_events` | 催化事件 | 行业事件日历、公司业绩/财报日期、产品发布/技术迭代时间表、现有 catalyst 数据 | 列举未来 6-12 个月具体催化事件，评估潜在影响力，无可预见催化 = 不过 |
| 7 | `valuation_level` | 估值水位 | PE/PB 历史分位数、PEG 比率、行业/可比公司估值对比、股价透支信号 | 综合历史分位/PEG/行业对比判断估值是否透支未来增长，PE 90%+ 分位且 PEG>2 = 大概率不过 |
| 8 | `fatal_risks` | 致命风险 | 风险事件数据、财务异常信号（商誉/应收/现金流）、监管风险、大股东质押/减持 | 扫描尚未被市场定价的致命风险（财务造假/重大监管/技术颠覆），有风险 = 不过 |

### 4.3 执行顺序

```
① 主线属性 → ② 景气周期 → ③ 3年空间 → ④ 竞争格局 → ⑤ 驱动因素 → ⑥ 催化事件 → ⑦ 估值水位 → ⑧ 致命风险
```

逻辑：先判"该不该做这个行业"（①-⑤行业层面），再看"时机对不对"（⑥-⑦催化/估值），最后"安全检查"（⑧风险）。行业不过就不浪费催化和估值分析。

### 4.4 LLM Prompt 结构

每个评估器的 prompt 统一格式：

```
你是一个A股行业分析师。请基于以下数据，判断【{criterion_name}】是否满足条件。

## 判定标准
{criterion_specific_rubric}

## 股票信息
股票: {stock_name} ({symbol})
行业: {industry_name}

## 数据
{collected_data_summary}

## 请返回 JSON
{
  "pass": true/false,
  "verdict": "2-3句话的定性判断，说明为什么通过/不过"
}
```

- `criterion_specific_rubric` 为每个准则硬编码的判定规则文本
- 数据通过 `data_summary`（文本版本）传入，不是 raw JSON
- 返回严格 JSON，后端用 `json.loads` 解析
- temperature=0.2 保证判定一致性

## 5. SSE 事件协议

| 事件名 | 触发时机 | data 内容 |
|---|---|---|
| `criterion_start` | 开始评估某准则 | `{criterion_id, criterion_name, index}` |
| `criterion_complete` | 某准则评估完成 | `CriterionResult` |
| `analysis_complete` | 全部分析结束 | `{final_decision, passed, failed, not_evaluated, stopped_at, summary}` |
| `error` | 某准则评估出错 | `{criterion_id, message}` |

附加：
- 心跳：每 15 秒发送 `:keepalive` 注释
- 取消：前端 abort SSE 连接，后端检测断开后取消 LLM 调用
- 无需 session 持久化，每次分析是一次性的

## 6. 前端 UI 设计

### 6.1 页面结构

```
┌─────────────────────────────────────────────────┐
│ SummaryBar: 股票名 | 通过/不过/未评估计数 | 结论 │
├─────────────────────────────────────────────────┤
│ 进度条: ████████░░░░░░░░░░░░░░░░ (8段)         │
├─────────────────────────────────────────────────┤
│ ① 市场主线属性  ✅ 通过                          │
│   "AI算力板块当前属于市场核心主线..."             │
│   ▶ 查看底层数据明细                             │
├─────────────────────────────────────────────────┤
│ ② 景气上行周期  ✅ 通过                          │
│   "光模块行业连续3个季度营收增速加速..."          │
│   ▶ 查看底层数据明细                             │
├─────────────────────────────────────────────────┤
│ ...                                              │
├─────────────────────────────────────────────────┤
│ ④ 竞争格局  ❌ 未通过                            │
│   "行业CR5从68%降至59%，毛利率连续下滑..."       │
│   ▶ 查看底层数据明细                             │
├─────────────────────────────────────────────────┤
│ ⑤⑥⑦⑧ 未评估（前置准则未通过）                   │
└─────────────────────────────────────────────────┘
```

### 6.2 卡片状态

| 状态 | 视觉表现 |
|---|---|
| `idle` | 灰色，圆形空心图标，文字"等待分析" |
| `running` | 蓝色/黄色，加载动画，文字"分析中..." |
| `pass` | 绿色边框，✓ 图标，LLM 结论 + 通过标签 |
| `fail` | 红色边框，✗ 图标，LLM 结论 + 未通过标签 |
| `not_evaluated` | 灰色，半透明，文字"未评估（前置准则未通过）" |

### 6.3 组件职责

| 组件 | 职责 |
|---|---|
| `BuyCriteriaPanel` | Tab 根组件。初始化 hook，渲染 SummaryBar + 进度条 + 8 张卡片。不含业务逻辑。 |
| `SummaryBar` | 股票名 + 通过/未通过/未评估计数 + 最终结论 + "重新分析"按钮 |
| `CriterionCard` | 单张准则卡片。接收 `CriterionResult`，展示 LLM 结论 + 可折叠数据明细。支持 5 种状态。 |
| `useBuyCriteria` | SSE 订阅 + 状态管理。管理 8 项准则状态（idle/running/pass/fail/not_evaluated）、分析进度、开始/停止控制。 |

## 7. 错误处理

| 场景 | 处理方式 |
|---|---|
| 数据拉取失败（API 超时/不可用） | 该准则标记 `pass: false`，verdict 说明原因，触发提前终止 |
| LLM 调用失败 | 重试 1 次，仍失败则该准则标记 error，分析终止 |
| LLM 返回非结构化/无法解析 | 重试 1 次，仍失败则标记 error |
| 全部 8 项通过但某项数据不完整 | verdict 中注明"基于不完整数据判断"，仍标记 pass |
| 用户中途取消 | 前端 abort SSE，后端优雅停止 |

## 8. 过渡方案

| 旧组件 | 处理方式 |
|---|---|
| `components/buyDecision/BuyDecisionWorkbench.tsx` | 删除 → 替换为 `components/buyCriteria/BuyCriteriaPanel.tsx` |
| `components/buyDecision/IndustryBetaChat.tsx` | 删除 → 行业分析内化到评估器 ①②③ |
| `hooks/useBuyDecisionWorkbench.ts` | 删除 → 替换为 `hooks/useBuyCriteria.ts` |
| `api/buyDecisionWorkbench.ts` | 删除 → 替换为 `api/buyCriteria.ts` |
| `src/services/buy_decision_workbench_service.py` | 删除 → 替换为 `src/services/buy_criteria/` |
| API 路由 `/buy-decision/workbench/*` | 删除 → 替换为 `/buy-decision/criteria/*` |
| `hooks/useTaskStream.ts` | **保留** — 新 hook 复用 |
| `tests/test_buy_decision_api.py` | 重写为 `tests/test_buy_criteria_api.py` |

预计代码量：旧 ~2600 行 → 新 ~1500 行，净减少约 1100 行。

## 9. 数据复用

新评估器复用现有数据拉取函数，但通过 `DataService` 层统一封装：

| 现有函数来源 | 复用给评估器 |
|---|---|
| `get_stock_info` | ①⑤⑧ |
| `get_sector_list` / `_fetch_sector_flow_industry` | ①②③ |
| `get_valuation_ratios` / `get_price_overdraft_signal` | ⑦ |
| `get_shareholder_structure` | ⑧ |
| `get_sentiment` / `get_social_sentiment` | ①⑤ |
| `get_risk_events` | ⑧ |
| `IndustryCycleService.get_report` | ②③ |
| catalyst 数据 | ⑥ |

`DataService` 不再直接调用 endpoint 函数，而是调用底层 service/repository 层，保持分层清晰。
