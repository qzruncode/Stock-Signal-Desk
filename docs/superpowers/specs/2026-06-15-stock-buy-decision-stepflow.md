# 个股买入判断页步骤流重设计

> 日期: 2026-06-15
> 状态: 已实现
> 范围: `apps/dsa-web/src/pages/StockAnalysisPage.tsx` + `apps/dsa-web/src/components/buyDecision/BuyDecisionWorkbench.tsx` + `apps/dsa-web/src/hooks/useBuyDecisionWorkbench.ts` + `apps/dsa-web/src/api/buyDecisionWorkbench.ts` + `api/v1/endpoints/buy_decision.py` + `src/services/buy_decision_workbench_service.py`

## 0. 实现快照

截至 2026-06-16，这份设计已经按下面的方式落地：

- 页面入口已从旧 `行业周期` 切换为 `买入判断`
- 6 步手动执行工作台已上线
- 页面刷新可恢复 workbench session
- 移动端已补 `sticky` 顶部信息区和底部当前步骤主按钮
- 步骤原始数据与最终原始模型输出默认折叠
- 后端已提供 `/buy-decision/workbench` 系列接口
- `industry_beta / mainline_position / company_benefit / buy_constraints / final_decision` 已抽成步骤 helper
- 会话级辅助数据缓存已接入
- `tests/test_buy_decision_api.py` 已通过

## 1. 目标纠偏

这个页面的目标不是判断“是不是主线”，而是判断：

- 这只股现在到底能不能买
- 为什么能买 / 为什么不能买
- 如果还不能买，卡在哪些条件

所以：

- `主线属性判定器` 只是买入判断中的一个证据模块
- `行业 β 判定器` 也是买入判断中的一个证据模块
- 最终结论必须是买入结论，不是单独输出“主线/非主线”

行业周期状态仍然要保留，但它只能作为最终买入判断的底层标签之一。

## 2. 页面原则

- 页面只有一条竖向进度
- 每一步由用户手动触发
- 每一步必须成功且拿到数据，下一步才能点
- 每一步只负责产出当前证据，必要时允许局部模型判断
- 最后一步才把完整证据包交给模型统一研判
- 页面设计简洁、高数据密度、移动端优先

不做的事：

- 不做花哨动效
- 不做大段原始流式输出主导页面
- 不把“主线属性”误当成最终目标

## 3. 最终输出

最后一步必须同时输出两层结果。

### 3.1 第一层：买入判断

- `可买入`
- `可跟踪等待`
- `暂不买入`
- `禁止追高`

### 3.2 第二层：行业周期标签

- `主线`
- `分支主线`
- `观察`
- `退潮`
- `非主线`

### 3.3 页面最终必须回答的问题

- 现在能不能买
- 这是趋势买点、分歧买点，还是仅观察标的
- 支持买入的核心证据是什么
- 阻止买入的核心约束是什么
- 后续需要验证哪些观察点

## 4. 步骤拆分

建议拆成 6 步。

### 步骤 1: 个股基础面与行业归属

目标：

- 校验股票基础资料
- 确认主营业务
- 确认行业归属和主营映射是否成立

输入：

- `symbol`

输出：

- `stock_profile`
  - `symbol`
  - `stock_name`
  - `industry_name`
  - `main_business`
  - `exchange`
  - `as_of_date`
- `industry_mapping`
  - `is_industry_relevant`
  - `industry_match_level`
  - `mapping_reason`
  - `business_keywords`
  - `industry_keywords`

成功门槛：

- `industry_name` 非空
- `main_business` 非空
- `mapping_reason` 非空

说明：

- 这一步不是在判断能不能买，而是在确认后面的买入判断有没有分析基础。

### 步骤 2: 行业周期与行业 β

目标：

- 判断行业是否处于上升周期
- 判断未来 3 年空间是否明确
- 判断是否存在严重价格战 / 内卷风险
- 输出 `行业 β 判定器`

输入：

- `symbol`
- `industry_name`

输出：

- `industry_beta_evidence`
  - `sector_snapshot`
  - `fund_flow`
  - `peer_group`
  - `industry_space`
  - `price_war_signal`
  - `driver_signals`
- `industry_beta_detector`
  - `passed`
  - `checklist`
  - `failed_reason`
  - `conclusion`

判定器必须覆盖：

- 行业处于上升周期
- 未来 3 年空间明确
- 不是严重价格战 / 内卷行业
- 有政策 / 技术 / 需求 / 供给变化驱动

成功门槛：

- `industry_beta_detector` 成功生成

### 步骤 3: 主线属性与市场位置

目标：

- 判断这只股是否处在当前市场主线 / 分支主线体系
- 判断它是不是冷门低估股、单纯蹭概念
- 输出 `主线属性判定器`

输入：

- `symbol`
- `industry_name`

输出：

- `mainline_evidence`
  - `market_mainline`
  - `theme_matches`
  - `heat_snapshot`
  - `concept_purity`
  - `driver_signals`
  - `catalyst_snapshot`
- `mainline_detector`
  - `passed`
  - `checklist`
  - `failed_reason`
  - `conclusion`

判定器必须覆盖：

- 当前属于市场主线 / 分支主线
- 不是冷门低估股
- 不是单纯蹭概念
- 主营业务能实际受益
- 有政策 / 技术 / 需求 / 供给变化驱动
- 未来 6-12 个月仍有催化

成功门槛：

- `mainline_detector` 成功生成

说明：

- 这一步依然不是最终买入结论，它只是市场位置判断。

### 步骤 4: 个股真实受益与弹性

目标：

- 判断主营是否真实受益
- 判断个股是否具备业绩、订单、产品、产能、估值、辨识度等买入弹性

输入：

- `symbol`

输出：

- `company_evidence`
  - `financial_snapshot`
  - `valuation_snapshot`
  - `stock_focus_snapshot`
  - `holder_snapshot`
  - `benefit_alignment`
  - `elasticity_signals`

成功门槛：

- `stock_focus_snapshot.focus_view` 非空
- `benefit_alignment` 非空
- `financial_snapshot` 或 `valuation_snapshot` 至少一个有效

说明：

- 这一步可以用小模型只回答一个问题：
  - `主营是否真实受益`

### 步骤 5: 风险与买点约束

目标：

- 判断当前是否存在不适合买入的约束
- 包括高位、追高、估值透支、风险事件、情绪过热等

输入：

- `symbol`

输出：

- `buy_constraints`
  - `risk_snapshot`
  - `sentiment_snapshot`
  - `valuation_risk`
  - `position_risk`
  - `chasing_risk`
  - `event_risk`
  - `observation_points`

成功门槛：

- `risk_snapshot` 有效
- 至少一个买点约束字段有效

说明：

- 这是直接服务于“能不能买”的关键步骤。
- 哪怕前面两个判定器都不错，只要这一步约束过强，最后也可能是 `暂不买入` 或 `禁止追高`。

### 步骤 6: 最终模型结论

目标：

- 把前五步的完整证据包交给模型统一研判
- 输出最终买入结论和行业周期标签

输入：

- `stock_profile`
- `industry_mapping`
- `industry_beta_detector`
- `mainline_detector`
- `company_evidence`
- `buy_constraints`
- 完整证据包

输出：

- `buy_decision`
  - `decision`
  - `decision_reason`
  - `entry_type`
  - `not_buy_reasons`
  - `must_watch_points`
- `industry_cycle`
  - `analysis_status`
  - `beneficiary_level`
  - `cycle_phase`
  - `prosperity_score`
  - `prosperity_judgement`
  - `core_logic`
- `final_summary`

交互要求：

- 这一步可以保留流式输出
- 但页面默认展示结构化结论，不以流式文本为主

## 4.1 步骤与需求覆盖矩阵

| 需求项 | 对应步骤 | 对应产物 |
|---|---|---|
| 当前属于市场主线 / 分支主线 | 步骤 3 | `mainline_detector.checklist` |
| 不是冷门低估股 | 步骤 3 | `mainline_evidence.heat_snapshot` |
| 不是单纯蹭概念 | 步骤 3 | `mainline_evidence.concept_purity` |
| 主营业务能实际受益 | 步骤 1、4 | `industry_mapping`、`benefit_alignment` |
| 有政策 / 技术 / 需求 / 供给变化驱动 | 步骤 2、3、4 | `driver_signals` |
| 未来 6-12 个月仍有催化 | 步骤 3、5 | `catalyst_snapshot`、`observation_points` |
| 行业处于上升周期 | 步骤 2 | `industry_beta_detector.checklist` |
| 未来 3 年空间明确 | 步骤 2 | `industry_space` |
| 不是严重价格战 / 内卷行业 | 步骤 2、5 | `price_war_signal`、`event_risk` |
| 当前是否适合买入 | 步骤 5、6 | `buy_constraints`、`buy_decision` |
| 输出主线 / 分支主线 / 观察 / 退潮 / 非主线 | 步骤 6 | `industry_cycle.analysis_status` |
| 输出最终买入判断 | 步骤 6 | `buy_decision.decision` |

## 5. 页面结构

页面只保留单列纵向步骤卡，不做双栏。

```text
┌──────────────────────────────┐
│ 买入判断                     │
│ 600519 贵州茅台              │
│ 白酒                         │
├──────────────────────────────┤
│ ① 个股基础面与行业归属  成功 │
│ 摘要: 主营与行业归属已确认   │
├──────────────────────────────┤
│ ② 行业周期与行业 β      当前 │
│ 操作: 开始本步              │
├──────────────────────────────┤
│ ③ 主线属性与市场位置        │
├──────────────────────────────┤
│ ④ 个股真实受益与弹性        │
├──────────────────────────────┤
│ ⑤ 风险与买点约束            │
├──────────────────────────────┤
│ ⑥ 最终模型结论              │
└──────────────────────────────┘
```

### 5.1 移动端设计要求

- 顶部固定显示股票简称、代码、行业
- 当前步骤展开，其余步骤折叠
- 每一步先显示摘要，再显示关键数据
- checklist 最多两列
- 原始 JSON / 原始模型输出默认折叠
- 底部固定当前步骤主按钮

### 5.2 每个步骤卡展示内容

- 步骤标题
- 状态：`未开始` / `执行中` / `成功` / `失败`
- 数据来源：`缓存` / `实时`
- 摘要
- 关键指标
- 错误信息
- `开始本步` / `重试本步` / `强制刷新`

### 5.3 每一步的关键指标建议

#### 步骤 1

- 所属行业
- 主营关键词数
- 行业映射强度

#### 步骤 2

- 板块排名
- 板块涨跌幅
- 资金流排名
- 价格战风险

#### 步骤 3

- 当前主线归属
- 当前热度等级
- 催化条数
- 概念纯度

#### 步骤 4

- 受益对齐等级
- 财务状态
- 估值状态
- 弹性信号数

#### 步骤 5

- 追高风险
- 位置风险
- 估值风险
- 事件风险

#### 步骤 6

- 买入判断
- 行业周期标签
- 进入方式
- 观察点数量

## 6. 页面状态机

```ts
type StepStatus = 'idle' | 'running' | 'success' | 'failed';

type BuyDecisionStepKey =
  | 'profile_mapping'
  | 'industry_beta'
  | 'mainline_position'
  | 'company_benefit'
  | 'buy_constraints'
  | 'final_decision';

interface BuyDecisionStepState<T = unknown> {
  key: BuyDecisionStepKey;
  status: StepStatus;
  fromCache?: boolean;
  startedAt?: string;
  finishedAt?: string;
  summary?: string;
  error?: string | null;
  data?: T;
}
```

规则：

1. 初始只开放步骤 1
2. 当前步骤成功后，下一步可点击，但不自动执行
3. 当前步骤失败后，只允许重试当前步骤
4. 页面刷新后恢复到第一个未成功步骤
5. 更换股票后重置步骤状态

## 6.1 页面文案口径

页面文案必须统一，不要混用“行业周期分析”“主线判断”“买入建议”三套说法。

建议统一：

- 页面标题：`买入判断`
- 页面副标题：`分步骤收集证据，最后生成买入结论`
- 步骤 2 标题：`行业周期与行业 β`
- 步骤 3 标题：`主线属性与市场位置`
- 最终结果标题：`最终买入结论`

禁用文案：

- `行业周期分析完成`
- `主线研判完成`

改为：

- `买入结论已生成`
- `当前股票买入判断已完成`

## 7. 接口拆分

旧接口：

- `GET /api/v1/stocks/industry-cycle/report`
- `POST /api/v1/stocks/industry-cycle/report/tasks`

保留兼容，但不再作为新页面主接口。

### 7.1 新接口建议

#### 初始化会话

`POST /api/v1/stocks/buy-decision/workbench`

请求：

```json
{
  "symbol": "600519",
  "force_reset": false
}
```

响应：

```json
{
  "session_id": "bd_600519_xxx",
  "symbol": "600519",
  "stock_name": "贵州茅台",
  "industry_name": "白酒",
  "current_step": "profile_mapping",
  "steps": {
    "profile_mapping": { "status": "idle" },
    "industry_beta": { "status": "idle" },
    "mainline_position": { "status": "idle" },
    "company_benefit": { "status": "idle" },
    "buy_constraints": { "status": "idle" },
    "final_decision": { "status": "idle" }
  }
}
```

#### 执行单步

`POST /api/v1/stocks/buy-decision/workbench/{session_id}/steps/{step_key}/run`

请求：

```json
{
  "force": false
}
```

通用响应：

```json
{
  "session_id": "bd_600519_xxx",
  "step": "industry_beta",
  "status": "success",
  "from_cache": true,
  "started_at": "2026-06-15T09:00:00",
  "finished_at": "2026-06-15T09:00:03",
  "summary": "行业处于上升周期，但价格战风险需要继续观察",
  "blocking": false,
  "next_step_enabled": true,
  "data": {}
}
```

#### 获取会话状态

`GET /api/v1/stocks/buy-decision/workbench/{session_id}`

响应：

```json
{
  "session_id": "bd_600519_xxx",
  "symbol": "600519",
  "stock_name": "贵州茅台",
  "industry_name": "白酒",
  "current_step": "company_benefit",
  "steps": {
    "profile_mapping": {
      "status": "success",
      "from_cache": false,
      "summary": "主营与行业映射清晰",
      "data": {}
    },
    "industry_beta": {
      "status": "success",
      "from_cache": true,
      "summary": "行业 beta 成立",
      "data": {}
    },
    "mainline_position": {
      "status": "success",
      "from_cache": false,
      "summary": "当前属于分支主线",
      "data": {}
    },
    "company_benefit": {
      "status": "idle"
    },
    "buy_constraints": {
      "status": "idle"
    },
    "final_decision": {
      "status": "idle"
    }
  }
}
```

#### 获取最终报告

`GET /api/v1/stocks/buy-decision/workbench/{session_id}/report`

响应：

```json
{
  "session_id": "bd_600519_xxx",
  "symbol": "600519",
  "buy_decision": {
    "decision": "可跟踪等待",
    "decision_reason": "行业 beta 和主线位置都不差，但当前买点约束仍偏强。",
    "entry_type": "分歧等待",
    "not_buy_reasons": ["短线位置偏高", "估值消化不充分"],
    "must_watch_points": ["回调后承接", "催化兑现", "价格战风险变化"]
  },
  "industry_cycle": {
    "analysis_status": "分支主线",
    "beneficiary_level": "核心受益",
    "cycle_phase": "发酵期",
    "prosperity_score": 76,
    "prosperity_judgement": "行业景气和市场关注度仍在强化。",
    "core_logic": "主营受益清晰，行业 beta 成立，但节奏上更适合等分歧。"
  },
  "final_summary": "这只股具备中期跟踪价值，但当前不是低风险直接买点。",
  "raw_stream_output": "",
  "model_used": "openai/glm-5.1"
}
```

### 7.2 步骤 key

- `profile_mapping`
- `industry_beta`
- `mainline_position`
- `company_benefit`
- `buy_constraints`
- `final_decision`

### 7.3 执行方式

- 步骤 1-5 用同步接口
- 步骤 6 默认同步流式输出
- 步骤 6 超时后可降级任务模式

### 7.4 步骤返回数据结构建议

#### `profile_mapping.data`

```json
{
  "stock_profile": {},
  "industry_mapping": {
    "is_industry_relevant": true,
    "industry_match_level": "high",
    "mapping_reason": "主营核心收入与所属行业高度一致",
    "business_keywords": ["高端白酒", "渠道", "品牌"],
    "industry_keywords": ["白酒", "消费升级", "高端消费"]
  }
}
```

#### `industry_beta.data`

```json
{
  "industry_beta_evidence": {
    "sector_snapshot": {
      "name": "白酒",
      "change_pct": 2.4,
      "rank": 5,
      "total": 86
    },
    "fund_flow": {
      "net_inflow": 12.8,
      "rank": 4,
      "total": 86
    },
    "peer_group": {
      "sample_size": 12,
      "sample_names": ["五粮液", "泸州老窖"]
    },
    "industry_space": {
      "space_level": "clear",
      "reason": "高端白酒需求和品牌集中度仍有提升空间"
    },
    "price_war_signal": {
      "level": "low",
      "reason": "未发现明显价格战信号"
    },
    "driver_signals": {
      "demand": ["高端消费恢复"],
      "supply": [],
      "policy": [],
      "technology": []
    }
  },
  "industry_beta_detector": {
    "passed": true,
    "conclusion": "行业 beta 成立",
    "failed_reason": null,
    "checklist": [
      {
        "item": "行业处于上升周期",
        "passed": true,
        "reason": "板块强度和资金流均处于上沿",
        "source": "板块表现 / 资金流"
      }
    ]
  }
}
```

#### `mainline_position.data`

```json
{
  "mainline_evidence": {
    "market_mainline": {
      "current_status": "分支主线",
      "matched_theme": "消费修复"
    },
    "theme_matches": ["消费修复", "高端消费"],
    "heat_snapshot": {
      "heat_level": "high",
      "discussion_count": 128
    },
    "concept_purity": {
      "level": "high",
      "reason": "主营收入与主题高度一致"
    },
    "driver_signals": {
      "demand": ["宴席消费恢复"]
    },
    "catalyst_snapshot": {
      "count": 3,
      "items": ["旺季验证", "渠道反馈", "业绩预期抬升"]
    }
  },
  "mainline_detector": {
    "passed": true,
    "conclusion": "主线属性成立",
    "failed_reason": null,
    "checklist": [
      {
        "item": "当前属于市场主线 / 分支主线",
        "passed": true,
        "reason": "匹配当前分支主线并持续强化",
        "source": "市场主线报告"
      }
    ]
  }
}
```

#### `company_benefit.data`

```json
{
  "company_evidence": {
    "financial_snapshot": {
      "revenue_yoy": 15.2,
      "profit_yoy": 18.4
    },
    "valuation_snapshot": {
      "pe_ttm": 28.5,
      "valuation_status": "medium"
    },
    "stock_focus_snapshot": {
      "focus_view": "主营受益直接，且高端价格带韧性较强",
      "finance_state": "healthy",
      "trading_state": "active"
    },
    "holder_snapshot": {
      "institution_holding_pct": 62.5
    },
    "benefit_alignment": "主营与行业逻辑高度一致",
    "elasticity_signals": ["提价能力", "渠道利润修复"]
  }
}
```

#### `buy_constraints.data`

```json
{
  "buy_constraints": {
    "risk_snapshot": {
      "high_risk_count": 0,
      "medium_risk_count": 1
    },
    "sentiment_snapshot": {
      "news_count": 16,
      "heat_level": "high"
    },
    "valuation_risk": "medium",
    "position_risk": "high",
    "chasing_risk": "high",
    "event_risk": "low",
    "observation_points": []
  }
}
```

#### `final_decision.data`

```json
{
  "buy_decision": {
    "decision": "可跟踪等待",
    "decision_reason": "基本面和行业位置都不差，但短线买点并不舒服。",
    "entry_type": "分歧等待",
    "not_buy_reasons": ["位置偏高", "追高性价比不足"],
    "must_watch_points": ["回调承接", "催化验证", "行业风险变化"]
  },
  "industry_cycle": {
    "analysis_status": "分支主线",
    "beneficiary_level": "核心受益",
    "cycle_phase": "发酵期",
    "prosperity_score": 76,
    "prosperity_judgement": "行业景气和市场热度仍在共振。",
    "core_logic": "主营真实受益，行业 beta 和主线属性均成立。"
  },
  "final_summary": "中期逻辑成立，但当前更适合等分歧而不是直接追高。"
}
```

## 7.5 最终买入判断的输出口径

后端最终模型输出必须严格限制在以下枚举内，不能自由发挥文案。

### `buy_decision.decision`

- `可买入`
- `可跟踪等待`
- `暂不买入`
- `禁止追高`

### `buy_decision.entry_type`

- `趋势跟随`
- `分歧低吸`
- `右侧确认`
- `仅观察`
- `禁止参与`

### 判定建议

#### `可买入`

适用条件：

- 行业 beta 成立
- 主线属性成立或接近成立
- 个股真实受益清晰
- 买点约束不强

#### `可跟踪等待`

适用条件：

- 中期逻辑成立
- 但节奏、位置、估值、催化兑现仍需等待

#### `暂不买入`

适用条件：

- 某些关键证据不足
- 或风险约束明显强于买入理由

#### `禁止追高`

适用条件：

- 逻辑可以成立
- 但当前位置/情绪/估值已经不支持继续追价

## 7.6 最终模型 Prompt 约束建议

最终步骤的大模型，不应该让它自由写宏大报告，而应强约束为“买入判断器”。

Prompt 里至少要明确：

- 你的任务不是判断股票好不好，而是判断“当前是否适合买入”
- 你必须先看买点约束，再看行业和主线逻辑
- `主线属性判定器` 和 `行业 β 判定器` 只是输入证据，不是最终结论
- 必须输出固定枚举的 `decision` 和 `entry_type`
- 不能输出模棱两可的中间词，如“偏可买”“中性偏强”

## 8. 服务端拆分建议

建议新增：

- `BuyDecisionWorkbenchService`

建议从当前 `IndustryCycleService` 抽出：

- `collect_stock_profile(symbol)`
- `build_industry_mapping(stock_profile)`
- `collect_industry_beta_evidence(symbol, industry_name, force=False)`
- `run_industry_beta_detector(evidence)`
- `collect_mainline_evidence(symbol, industry_name, force=False)`
- `run_mainline_detector(evidence)`
- `collect_company_benefit_evidence(symbol, force=False)`
- `collect_buy_constraints(symbol, force=False)`
- `generate_buy_decision_report(evidence_bundle, on_text=None)`

### 8.1 服务端职责边界

`BuyDecisionWorkbenchService`

- 管理 `session_id`
- 管理步骤依赖
- 执行单步
- 读取/写入步骤级缓存
- 汇总完整证据包
- 调用最终模型结论

`IndustryCycleService`

- 保留旧接口兼容
- 作为部分底层证据方法的承载方

### 8.2 依赖关系

```text
profile_mapping
  -> industry_beta
  -> mainline_position
  -> company_benefit
  -> buy_constraints
  -> final_decision
```

额外约束：

- `final_decision` 必须依赖前五步全部成功
- 任一步 `failed`，后续步骤一律禁用
- 任一步 `success but blocking=true`，后续步骤也禁用

### 8.3 blocking 语义

`status=success` 不代表一定能继续。

例如：

- 第 2 步成功取到行业数据，但判定器明确显示行业严重内卷
- 第 5 步成功取到风险数据，但 `chasing_risk=high`

这类情况可以有两种策略：

1. `blocking=true`
   直接禁止下一步
2. `blocking=false` 但打强风险标签
   允许继续，让最终模型统一裁决

建议：

- 步骤 1 如果基础资料缺失，`blocking=true`
- 步骤 2-5 默认 `blocking=false`
- 是否可买由最终一步统一裁决

理由：

- 页面目标是“判断能不能买”，不是中途就把用户拦死
- 中间步骤更适合提供约束，不适合过早替最终模型做决策

### 8.4 旧服务复用建议

当前 `IndustryCycleService` 里已经有这些可直接复用的能力：

- 主线报告和匹配证据
- 板块表现和资金流
- peer group
- valuation / sentiment / risk snapshot
- detector checklist 结构

建议做法：

- 先复用现有证据采集方法和 detector 结构
- 新增一层 `BuyDecisionWorkbenchService` 负责重新编排
- 不要一开始就重写底层采集逻辑

这样可以先把页面和接口跑通，再决定是否重构底层 service

## 9. 缓存建议

- `stocks:buy_decision:profile_mapping:v1:{symbol}:{date}`
- `stocks:buy_decision:industry_beta:v1:{symbol}:{date}`
- `stocks:buy_decision:mainline_position:v1:{symbol}:{date}`
- `stocks:buy_decision:company_benefit:v1:{symbol}:{date}`
- `stocks:buy_decision:buy_constraints:v1:{symbol}:{date}`
- `stocks:buy_decision:final_decision:v1:{symbol}:{date}`

收益：

- 每一步失败只重跑局部
- 用户刷新页面可以继续
- 重复查看同一股票可以复用步骤结果

## 10. 前端组件拆分建议

```text
apps/dsa-web/src/
├── components/buyDecision/
│   ├── BuyDecisionWorkbench.tsx
│   ├── BuyDecisionStepCard.tsx
│   ├── BuyDecisionStepDetail.tsx
│   ├── BuyDecisionEvidencePreview.tsx
│   ├── BuyDecisionDetectorChecklist.tsx
│   └── BuyDecisionFinalReport.tsx
├── hooks/
│   └── useBuyDecisionWorkbench.ts
└── api/
    └── buyDecisionWorkbench.ts
```

### 10.1 Hook 职责

`useBuyDecisionWorkbench.ts`

- 初始化 workbench 会话
- 维护 `activeStep`
- 执行单步
- 管理步骤 loading / error / cache 状态
- 页面刷新恢复
- 拉取最终报告

建议暴露：

```ts
type UseBuyDecisionWorkbenchResult = {
  state: BuyDecisionWorkbenchState | null;
  initializing: boolean;
  initialize: (symbol: string, forceReset?: boolean) => Promise<void>;
  runStep: (step: BuyDecisionStepKey, force?: boolean) => Promise<void>;
  refresh: () => Promise<void>;
  loadReport: () => Promise<void>;
};
```

### 10.2 移动端步骤卡信息层级

每张步骤卡分三层：

1. 头部
   - 步骤号
   - 标题
   - 状态标签
2. 中部
   - 一行摘要
   - 2-4 个关键指标
3. 底部
   - 主按钮
   - 展开详情

原则：

- 默认一屏只处理一个步骤
- 当前步骤详情展开，其他步骤只保留摘要
- checklist 放在详情区，不要默认全部展开

### 10.3 最终结论页信息顺序

最终结论页建议按这个顺序展示：

1. `买入判断`
2. `一句话结论`
3. `为什么可以买 / 为什么不能买`
4. `必须观察的点`
5. `行业周期标签`
6. `两个判定器结果`
7. `原始模型输出`

原因：

- 用户先关心“买不买”
- 其次关心“原因”
- 最后才关心底层标签和原始输出

### 10.4 实现时的文件建议

后端：

- `api/v1/endpoints/buy_decision.py`
- `src/services/buy_decision_workbench_service.py`

前端：

- `apps/dsa-web/src/api/buyDecisionWorkbench.ts`
- `apps/dsa-web/src/hooks/useBuyDecisionWorkbench.ts`
- `apps/dsa-web/src/components/buyDecision/BuyDecisionWorkbench.tsx`
- `apps/dsa-web/src/components/buyDecision/BuyDecisionStepCard.tsx`
- `apps/dsa-web/src/components/buyDecision/BuyDecisionFinalReport.tsx`

兼容期：

- 原 `industry-cycle` 模式先保留
- 新页面可以先挂在 `industry-cycle` tab 下替换内容
- 稳定后再决定是否调整路由和命名

## 11. 这版方案和原方案的根本区别

旧理解是：

- 页面目标是行业周期分析
- 主线属性是核心结论

新理解是：

- 页面目标是买入判断
- 行业 β 和主线属性只是中间证据
- 最终必须输出“能不能买”

## 12. 实施顺序

### Phase 1

- 抽服务方法
- 落步骤接口
- 保留旧接口兼容

交付物：

- `BuyDecisionWorkbenchService`
- `/buy-decision/workbench` 系列接口
- 步骤级缓存

### Phase 2

- 新建买入判断工作台页面
- 替换原行业周期页交互

交付物：

- `useBuyDecisionWorkbench`
- `BuyDecisionWorkbench` 及步骤卡组件
- 移动端单列步骤流

### Phase 3

- 补移动端交互细节
- 压缩 checklist 展示
- 优化最终买入结论排版

交付物：

- 最终结论卡优化
- checklist 高密度展示
- 页面恢复和强制刷新体验

## 13. 结论

这个页面应该被定义成：

- `个股买入判断工作台`

不是：

- `行业周期分析页`

真正驱动页面结构的，不是“如何展示主线分析”，而是“如何把买入所需证据一步步收齐，并在最后给出可信的买入判断”。

下一步进入开发时，最重要的约束只有一句：

- 中间步骤负责提供证据，最后一步才负责回答“现在能不能买”。
