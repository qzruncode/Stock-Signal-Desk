# 买入判断系统重构 — 独立维度分析

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将买入判断 8 个准则重构为"每步独立分析，直接消费原始数据"，消除 LLM 结论喂给另一个 LLM 的链式结构。

**Architecture:** 每个评估器直接从现有端点/数据源取原始数据，不再通过 `IndustryCycleService` 的 LLM 分析报告中转。4 个存在问题的评估器重写 `collect_data`，其他 4 个保持现状。

**Tech Stack:** Python 3, FastAPI, SQLite cache, LLM (call_ai_structured)

---

## 当前问题总结

### 依赖链

```
IndustryCycleService.get_report() (LLM #1 分析)
  → prosperity_score=72, cycle_phase="发酵期", β_detector={passed:true},
    policy_drivers=..., tech_drivers=..., cr5=..., competition_intensity=...
  ↓
prosperity_cycle / growth_space / growth_drivers / competition_landscape (LLM #2 消费)
  → 把 LLM #1 的结论当"客观输入"传给 LLM #2
```

### 8 个准则的数据来源现状

| # | 准则 | 数据来源 | 状态 |
|---|------|---------|------|
| ① | 市场主线 | `MarketThemeService.get_model_report()` | ✅ 直接消费原始证据，无需改 |
| ② | 景气上行周期 | `IndustryCycleService` (LLM 生成) | ❌ **需改** |
| ③ | 未来3年空间 | `IndustryCycleService` β_detector | ❌ **需改** |
| ④ | 竞争格局 | `IndustryCycleService` CR5/margin | ❌ **需改** |
| ⑤ | 驱动因素 | `IndustryCycleService` drivers | ❌ **需改** |
| ⑥ | 催化事件 | `DataService.get_catalyst_data()` (端点不存在，返回空) | ❌ **需改** |
| ⑦ | 估值水位 | `get_valuation_ratios()` (原始数据) | ✅ 无需改 |
| ⑧ | 致命风险 | `get_risk_events()` + `get_shareholder_structure()` (原始数据) | ✅ 无需改 |

---

## 文件结构

### 需修改的文件

| 文件 | 变更内容 |
|------|---------|
| `src/services/buy_criteria/evaluators/prosperity_cycle.py` | 移除 `industry_cycle_report` 依赖，改为直接取板块排名、资金流、财务增速、PMI 原始数据 |
| `src/services/buy_criteria/evaluators/growth_space.py` | 移除 β_detector 依赖，改为直接取研报预测、财务增长、新闻线索原始数据 |
| `src/services/buy_criteria/evaluators/growth_drivers.py` | 移除 `industry_cycle_report` 依赖，改为直接取政策新闻、研报技术描述、需求数据 |
| `src/services/buy_criteria/evaluators/competition_landscape.py` | 移除 `industry_cycle_report` 依赖，改为直接取毛利率、板块排名、同行对比原始数据 |
| `src/services/buy_criteria/evaluators/catalyst_events.py` | 移除不存在的 catalyst 端点依赖，改为直接取公告、新闻、研报中可预见的催化事件 |
| `src/services/buy_criteria/data_service.py` | 添加缺失的 raw data 方法（行业资金流、新闻搜索、公告等） |

### 不变的文件

| 文件 | 原因 |
|------|------|
| `src/services/buy_criteria/evaluators/mainline_position.py` | 已直接消费 `MarketThemeService` 原始证据 |
| `src/services/buy_criteria/evaluators/valuation_level.py` | 已直接消费 `get_valuation_ratios` 原始数据 |
| `src/services/buy_criteria/evaluators/fatal_risks.py` | 已直接消费原始风险/股东/财务数据 |
| `src/services/buy_criteria/base.py` | 基类无需修改 |
| `src/services/buy_criteria/prompts/rubrics.py` | 判定标准文本无需修改 |

---

## Task 1: 重写 prosperity_cycle — 直接取原始数据

**Files:**
- Modify: `src/services/buy_criteria/evaluators/prosperity_cycle.py`
- Test: `tests/test_buy_criteria.py`

当前 `prosperity_cycle` 从 `IndustryCycleService.get_report()` 拿 `prosperity_score`、`cycle_phase`、`β_detector`，然后把 `prosperity_score=72` 塞进 prompt。

### 改动方向

改为直接从以下原始数据源取数：

1. **板块排名** — 通过 `DataService.get_sector_list("industry")` 拿到行业板块涨跌幅排名
2. **资金流** — 通过 `DataService.get_sector_flow_industry()` 拿到行业资金净流入
3. **财务增速** — 通过 `DataService.get_financials()` 拿最近 4 季度营收同比/环比（已有）
4. **宏观 PMI** — 通过 `DataService.get_macro_indicator("PMI")` 拿最新 PMI 及趋势（已有）
5. **不再使用 `IndustryCycleService`**

### 代码变更

**prosperity_cycle.py 的 `collect_data` 方法**，将 `industry_cycle_report` 调用替换为原始数据：

```python
# 改前（当前第104-124行）
report_payload = ds.get_industry_cycle_report(symbol)
cycle = _as_dict(report_payload.get("industry_cycle"))
# ... 取 prosperity_score, β_detector 等

# 改后（删除上面的代码，替换为）
# 行业板块排名与资金流 — 从原始端点直接取
try:
    sectors = ds.get_sector_list("industry")
    industry = stock_info.get("industry", "")
    raw["sector_data"] = {
        "items": [
            {"name": s["name"], "rank": s.get("rank"), "change_pct": s.get("change_pct")}
            for s in (sectors.get("items") or [])[:20]
        ],
        "target_industry": None,
    }
    for s in (sectors.get("items") or []):
        if s.get("name") == industry:
            raw["sector_data"]["target_industry"] = {
                "name": s["name"], "rank": s.get("rank"),
                "change_pct": s.get("change_pct"), "total_amount": s.get("total_amount"),
            }
            break
except Exception as exc:
    logger.warning("[prosperity] sector_list failed: %s", exc)

try:
    fund_flow = ds.get_sector_flow_industry()
    raw["fund_flow"] = {
        "inflow": [{"name": f["name"], "net_flow": f.get("net_flow"), "change_pct": f.get("change_pct")} for f in (fund_flow or [])[:10]],
        "outflow": [{"name": f["name"], "net_flow": f.get("net_flow"), "change_pct": f.get("change_pct")} for f in (fund_flow or [])[-10:]],
    }
except Exception as exc:
    logger.warning("[prosperity] sector_flow failed: %s", exc)
```

**prompt summary 部分**（当前第176-232行），移除所有引用 `prosperity_score`、`β_detector`、`cycle_phase` 的行，替换为板块排名和资金流的原始数据展示：

```python
# 改前（第183-198行）
lines.extend([
    "## 行业周期报告",
    f"- 报告状态：{'生成中/不可用' if ic.get('report_pending') else '可用'}",
])
if ic.get("prosperity_score") is not None:
    lines.append(f"- 行业景气度评分：{ic['prosperity_score']} / 100")
lines.extend([
    f"- 景气判断：{ic.get('prosperity_judgement') or '缺失'}",
    f"- 周期阶段：{ic.get('cycle_phase') or '缺失'}；原因：{ic.get('cycle_phase_reason') or '缺失'}",
    f"- 行业β结论：{beta.get('conclusion') or '缺失'}；通过：{beta.get('passed')}",
])
# ... β_detector 各子项

# 改后
lines.extend([
    "## 行业板块与资金流",
])
target = raw.get("sector_data", {}).get("target_industry")
if target:
    lines.append(f"- 本行业[{target['name']}]：板块排名第{target.get('rank', '?')}名，涨跌幅{target.get('change_pct', '?')}%")
else:
    lines.append(f"- 本行业[{industry}]：未匹配到板块排名数据")
lines.append(f"- 板块前5名：{'; '.join(f'{s[\"name\"]} (#{s[\"rank\"]})' for s in raw.get('sector_data', {}).get('items', [])[:5]) or '缺失'}")
lines.append("")

ff = raw.get("fund_flow", {})
if ff.get("inflow"):
    lines.append(f"- 资金净流入前3：{'; '.join(f'{f[\"name\"]} (+{f[\"net_flow\"]})' for f in ff['inflow'][:3])}")
else:
    lines.append("- 资金流数据：缺失")
lines.append("")
```

**数据缺口部分**也需要更新，移除 `prosperity_score` 缺失检查：

```python
# 改前（第165-174行）
gaps: list[str] = []
if ic.get("prosperity_score") is None:
    gaps.append("行业景气度评分缺失")
if not financial_items:
    gaps.append("最近财务增速缺失")
if not pmi_latest:
    gaps.append("PMI缺失")
# ...

# 改后
gaps: list[str] = []
if not financial_items:
    gaps.append("最近财务增速缺失")
if not pmi_latest:
    gaps.append("PMI缺失")
elif pmi.get("is_stale"):
    gaps.append("宏观PMI数据可能过期")
if not target:
    gaps.append("行业板块排名未匹配")
```

- [ ] **Step 1: 修改 prosperity_cycle.py collect_data 方法**
  - 删除 `industry_cycle_report` 调用及所有相关变量提取（第104-124行）
  - 添加板块排名和资金流原始数据获取
  - 保留财务增速和 PMI 的已有逻辑
  - 重写 prompt summary 构建部分（第176-232行），展示原始数据而非 LLM 结论
  - 更新数据缺口检查，移除 prosperity_score 相关检查

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m pytest tests/test_buy_criteria.py -v -k prosperity 2>&1 | tail -30
```

Expected: Tests pass (prosperity_cycle no longer depends on IndustryCycleService)

- [ ] **Step 3: 编译检查**

```bash
python3 -m compileall -q src/services/buy_criteria/evaluators/prosperity_cycle.py
```

Expected: No errors

---

## Task 2: 重写 growth_space — 直接取原始数据

**Files:**
- Modify: `src/services/buy_criteria/evaluators/growth_space.py`
- Test: `tests/test_buy_criteria.py`

当前 `growth_space` 从 `IndustryCycleService` 拿 `β_detector` 中的"未来 3 年空间明确"判定项，这是 LLM 的主观结论。

### 改动方向

改为直接消费：

1. **券商研报与盈利预测** — 通过 `DataService.get_research_report()` 拿最近 3 年研报（已有）
2. **公司财务增长** — 通过 `DataService.get_financials()` 拿 4 季度数据（已有）
3. **新闻中的需求线索** — 通过 `DataService.search_news()` 拿最近 180 天新闻（已有）
4. **移除 `industry_cycle_report` 依赖**，移除 `β_detector` 相关所有代码

### 代码变更

**growth_space.py 的 `collect_data` 方法**，删除 industry_cycle 部分（第117-138行），保留并强化已有的 financials/research/news 获取：

```python
# 删除第117-138行（industry_cycle_report 调用）
# 删除 _find_detector_item 辅助函数（不再需要）
# 删除 prompt 中引用 β_detector 的行（第199-206行）

# 新增：从研报中提取盈利预测和行业规模线索
def _extract_forecast_signals(research_items: list[dict]) -> str:
    """从研报中提取分析师对行业增速/空间的看法"""
    signals = []
    for item in research_items[:6]:
        forecasts = item.get("profit_forecasts") or []
        if forecasts:
            for f in forecasts[:2]:
                if f.get("revenue_growth") or f.get("industry_growth"):
                    signals.append(
                        f"{item.get('org')} ({item.get('publish_date')}): "
                        f"{f.get('revenue_growth') or f.get('industry_growth')}"
                    )
    return "；".join(signals) if signals else "研报中无明确增速预测"
```

**prompt summary 部分更新**，移除 β_detector 相关行：

```python
# 删除第199-206行
# 替换为直接从研报和新闻中展示增长线索
lines.extend([
    "## 研报盈利预测与行业增速线索",
    f"- 研报数量：{research.get('count', 0)}；正向评级：{research.get('positive_count', 0)}",
    f"- 盈利预测汇总：{_format_forecasts_summary(research_items)}",
    "",
])
```

- [ ] **Step 1: 修改 growth_space.py collect_data 方法**
  - 删除 `industry_cycle_report` 调用及相关变量提取（第117-138行）
  - 删除 `_find_detector_item` 函数
  - 删除 `_as_dict(ic.get("industry_beta_detector"))` 相关代码（第179-186行）
  - 在 prompt summary 中移除 β_detector 相关展示（第199-206行）
  - 保留并优化 financials/research/news 的原始数据展示
  - 更新"缺失字段"部分，移除对 LLM 分析结果的依赖描述

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m pytest tests/test_buy_criteria.py -v -k growth_space 2>&1 | tail -30
```

Expected: Tests pass

- [ ] **Step 3: 编译检查**

```bash
python3 -m compileall -q src/services/buy_criteria/evaluators/growth_space.py
```

---

## Task 3: 重写 growth_drivers — 直接取原始数据

**Files:**
- Modify: `src/services/buy_criteria/evaluators/growth_drivers.py`
- Test: `tests/test_buy_criteria.py`

当前 `growth_drivers` 从 `IndustryCycleService` 拿 `policy_drivers`、`tech_drivers`、`demand_drivers`，全是 LLM 总结的文本。

### 改动方向

改为直接从原始数据中提取驱动因素证据：

1. **政策驱动** — 搜索近 6 个月新闻/公告中的政策关键词（国家级/部委级）
2. **技术驱动** — 从研报中提取技术突破/迭代相关描述
3. **需求驱动** — 从新闻中提取订单/出货量/装机量增长信息
4. **移除 `industry_cycle_report` 依赖**

### 代码变更

**growth_drivers.py 的 `collect_data` 方法**，完全重写：

```python
# 改前（第20-36行）
report = ds.get_industry_cycle_report(symbol)
raw["industry_cycle"] = {
    "policy_drivers": report.get("policy_drivers"),
    "tech_drivers": report.get("tech_drivers"),
    "demand_drivers": report.get("demand_drivers"),
    ...
}

# 改后
ds = DataService()
raw: dict[str, Any] = {}

# 政策驱动 — 从新闻/公告中搜索政策关键词
try:
    news = ds.search_news(symbol, days=180)
    news_items = _list_of_dicts(news.get("items"))[:20]
    policy_keywords = ["政策", "规划", "部委", "发改委", "工信部", "国务院", "中央",
                       "补贴", "支持", "指导意见", "行动方案", "十四五", "专项"]
    policy_items = [
        {"title": n.get("title"), "source": n.get("source"),
         "time": n.get("publish_time")}
        for n in news_items
        if any(kw in (n.get("title") or "") + (n.get("summary") or "") for kw in policy_keywords)
    ]
    raw["policy_evidence"] = {
        "items": policy_items[:5],
        "count": len(policy_items),
    }
except Exception as exc:
    logger.warning("[drivers] news failed: %s", exc)

# 技术驱动 — 从研报中提取技术相关描述
try:
    research = ds.get_research_report(symbol, days=365)
    research_items = _list_of_dicts(research.get("items"))[:10]
    tech_keywords = ["技术突破", "技术迭代", "新一代", "量产", "商用", "升级", "创新"]
    tech_items = [
        {"title": r.get("title"), "org": r.get("org"), "date": r.get("publish_date"),
         "summary": r.get("summary", "")[:200]}
        for r in research_items
        if any(kw in (r.get("title") or "") + (r.get("summary") or "") for kw in tech_keywords)
    ]
    raw["tech_evidence"] = {
        "items": tech_items[:5],
        "count": len(tech_items),
    }
except Exception as exc:
    logger.warning("[drivers] research failed: %s", exc)

# 需求驱动 — 从新闻中提取需求/订单线索
try:
    demand_keywords = ["订单", "出货", "装机", "销量", "需求", "产能", "扩产", "供不应求"]
    demand_items = [
        {"title": n.get("title"), "source": n.get("source"),
         "time": n.get("publish_time")}
        for n in news_items
        if any(kw in (n.get("title") or "") + (n.get("summary") or "") for kw in demand_keywords)
    ]
    raw["demand_evidence"] = {
        "items": demand_items[:5],
        "count": len(demand_items),
    }
except Exception as exc:
    logger.warning("[drivers] demand extraction failed: %s", exc)
```

**prompt summary 部分**：

```python
# 改前（第49-61行）
parts = []
if ic.get("policy_drivers"):
    parts.append(f"政策驱动：{ic['policy_drivers']}")
...
summary = "；".join(parts) if parts else "数据获取不完整"

# 改后
lines = [
    "## 政策驱动证据",
]
pe = raw.get("policy_evidence", {})
if pe.get("items"):
    for item in pe["items"][:5]:
        lines.append(f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:180]}")
else:
    lines.append(f"- 近6个月新闻中未发现{len(pe.get('items', []))}条政策相关报道")
lines.extend([
    "",
    "## 技术驱动证据",
])
te = raw.get("tech_evidence", {})
if te.get("items"):
    for item in te["items"][:5]:
        lines.append(f"- [{item.get('date', '?')}] {item.get('org', '?')}：{item.get('title', '')[:120]}；{item.get('summary', '')}")
else:
    lines.append(f"- 研报中未发现{te.get('count', 0)}条技术突破相关描述")
lines.extend([
    "",
    "## 需求驱动证据",
])
de = raw.get("demand_evidence", {})
if de.get("items"):
    for item in de["items"][:5]:
        lines.append(f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:180]}")
else:
    lines.append(f"- 新闻中未发现{de.get('count', 0)}条需求/订单增长线索")
lines.extend([
    "",
    "## 判断约束",
    "- 仅基于上方实际证据判断，不得编造政策/技术/需求线索。",
    "- 如果某一类驱动力证据充足（有具体政策文件/技术突破报道/订单增长数据），则该类驱动成立。",
    "- 三类驱动至少有一种明确成立才判为通过。",
])
summary = "\n".join(lines)
```

- [ ] **Step 1: 完全重写 growth_drivers.py collect_data 方法**
  - 删除 `industry_cycle_report` 调用（第24-36行）
  - 实现政策/技术/需求三类证据的关键词搜索提取
  - 重写 prompt summary 构建
  - 移除 `_compact` 相关的未使用辅助代码

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m pytest tests/test_buy_criteria.py -v -k growth_drivers 2>&1 | tail -30
```

- [ ] **Step 3: 编译检查**

```bash
python3 -m compileall -q src/services/buy_criteria/evaluators/growth_drivers.py
```

---

## Task 4: 重写 competition_landscape — 直接取原始数据

**Files:**
- Modify: `src/services/buy_criteria/evaluators/competition_landscape.py`
- Test: `tests/test_buy_criteria.py`

当前 `competition_landscape` 从 `IndustryCycleService` 拿 `concentration_cr5`、`gross_margin_trend`、`competition_intensity`、`price_war_signals`，全是 LLM 分析结果。

### 改动方向

改为直接消费：

1. **毛利率数据** — 从 `DataService.get_valuation_ratios()` 拿当前毛利率和行业均值（已有）
2. **板块排名** — 从 `DataService.get_sector_list("industry")` 拿行业集中度信号
3. **同行对比** — 从研报中提取同行竞争格局描述
4. **移除 `industry_cycle_report` 依赖**

### 代码变更

**competition_landscape.py 的 `collect_data` 方法**：

```python
# 改前（第24-37行）
report = ds.get_industry_cycle_report(symbol)
raw["industry_cycle"] = {
    "concentration_cr5": report.get("concentration_cr5"),
    "gross_margin_trend": report.get("gross_margin_trend"),
    "competition_intensity": report.get("competition_intensity"),
    "price_war_signals": report.get("price_war_signals"),
    "peer_comparison": report.get("peer_comparison"),
}

# 改后
ds = DataService()
raw: dict[str, Any] = {}

# 毛利率数据 — 直接从估值端点取
try:
    valuation = ds.get_valuation_ratios(symbol)
    industry_avg = valuation.get("industry_average") or {}
    raw["margin_data"] = {
        "gross_margin": valuation.get("gross_margin"),
        "net_margin": valuation.get("net_margin"),
        "industry_avg_gross_margin": industry_avg.get("gross_margin"),
        "industry_avg_net_margin": industry_avg.get("net_margin"),
    }
except Exception as exc:
    logger.warning("[competition] valuation failed: %s", exc)

# 毛利率趋势 — 从财务数据中取最近 4 季度毛利率
try:
    financials = ds.get_financials(symbol, periods=4, force=True)
    items = _list_of_dicts(financials.get("items"))[:4]
    raw["margin_trend"] = {
        "items": [
            {"date": item.get("report_date"), "gross_margin": item.get("gross_margin")}
            for item in items
        ],
    }
except Exception as exc:
    logger.warning("[competition] financials failed: %s", exc)

# 板块排名 — 判断行业是否过度分散
try:
    sectors = ds.get_sector_list("industry")
    raw["sector_ranking"] = {
        "items": [{"name": s["name"], "rank": s.get("rank"), "change_pct": s.get("change_pct")}
                  for s in (sectors.get("items") or [])[:30]],
    }
except Exception as exc:
    logger.warning("[competition] sectors failed: %s", exc)

# 价格战信号 — 从新闻中搜索降价/促销关键词
try:
    news = ds.search_news(symbol, days=180)
    news_items = _list_of_dicts(news.get("items"))[:20]
    price_war_keywords = ["降价", "价格战", "促销", "内卷", "毛利率下滑", "降价促销"]
    price_war_items = [
        {"title": n.get("title"), "source": n.get("source"), "time": n.get("publish_time")}
        for n in news_items
        if any(kw in (n.get("title") or "") + (n.get("summary") or "") for kw in price_war_keywords)
    ]
    raw["price_war_signals"] = {
        "items": price_war_items[:5],
        "count": len(price_war_items),
    }
except Exception as exc:
    logger.warning("[competition] price war search failed: %s", exc)
```

**prompt summary 部分**：

```python
lines = [
    "## 毛利率数据",
]
md = raw.get("margin_data", {})
if md.get("gross_margin") is not None:
    lines.append(f"- 当前毛利率：{md['gross_margin']}%")
if md.get("industry_avg_gross_margin") is not None:
    lines.append(f"- 行业平均毛利率：{md['industry_avg_gross_margin']}%")
mt = raw.get("margin_trend", {})
if mt.get("items"):
    lines.append(f"- 最近4季度毛利率趋势：{' → '.join(str(item.get('gross_margin', '?')) + '%' for item in mt['items'])}")
lines.extend([
    "",
    "## 行业板块竞争格局",
])
sr = raw.get("sector_ranking", {})
if sr.get("items"):
    lines.append(f"- 板块共{len(sr['items'])}个行业参与排名，本行业排名：{_find_target_rank(sr['items'], industry)}")
else:
    lines.append("- 板块排名数据缺失")
lines.extend([
    "",
    "## 价格战信号",
])
pw = raw.get("price_war_signals", {})
if pw.get("count", 0) > 0:
    lines.append(f"- 发现{pw['count']}条价格战/内卷相关报道：")
    for item in pw.get("items", [])[:5]:
        lines.append(f"  - [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:160]}")
else:
    lines.append("- 近6个月未发现明显价格战/内卷信号")
lines.extend([
    "",
    "## 判断约束",
    "- 毛利率连续下滑 + 价格战信号 = 内卷风险高。",
    "- 毛利率稳定/提升 + 无明显价格战信号 = 竞争格局健康。",
    "- 行业板块参与企业数量多（>20）且排名接近 = 可能过度分散。",
])
summary = "\n".join(lines)
```

- [ ] **Step 1: 完全重写 competition_landscape.py collect_data 方法**
  - 删除 `industry_cycle_report` 调用（第24-37行）
  - 实现毛利率数据/趋势、板块排名、价格战信号搜索
  - 重写 prompt summary 构建
  - 保留 `get_valuation_ratios` 已有逻辑

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m pytest tests/test_buy_criteria.py -v -k competition 2>&1 | tail -30
```

- [ ] **Step 3: 编译检查**

```bash
python3 -m compileall -q src/services/buy_criteria/evaluators/competition_landscape.py
```

---

## Task 5: 重写 catalyst_events — 从原始数据中提取催化

**Files:**
- Modify: `src/services/buy_criteria/evaluators/catalyst_events.py`
- Test: `tests/test_buy_criteria.py`

当前 `catalyst_events` 调用 `DataService.get_catalyst_data()`，但该端点不存在，返回空字典。

### 改动方向

直接从已有的原始数据中提取可预见的催化事件：

1. **公告中的催化** — 从 `DataService.get_risk_events()` 或公告端点提取未来事件
2. **新闻中的催化** — 从新闻标题/摘要中提取展会、政策窗口期、产品发布等
3. **研报中的催化** — 从研报标题/摘要中提取业绩拐点、技术迭代节点
4. **财务中的催化** — 从财报日历中提取下季度财报发布时间

### 代码变更

**catalyst_events.py 的 `collect_data` 方法**：

```python
# 改前（第24-34行）
catalyst = ds.get_catalyst_data(symbol)
raw["catalyst"] = {
    "events": catalyst.get("events") or catalyst.get("catalysts") or [],
    "summary": catalyst.get("summary") or catalyst.get("catalyst_summary"),
    "timeframe": catalyst.get("timeframe"),
}

# 改后
ds = DataService()
raw: dict[str, Any] = {}

# 从公告中提取催化事件（政策窗口、业绩发布、产品发布等）
try:
    announcements = ds.get_risk_events(symbol, days=90)  # 公告也是通过 risk_events 聚合的
    ann_items = _list_of_dicts(announcements.get("items"))[:10]
    catalyst_keywords = ["发布", "召开", "投产", "量产", "签约", "中标", "投产",
                         "股东大会", "财报", "业绩", "投产", "扩产", "投产仪式"]
    catalyst_items = [
        {"title": a.get("title"), "date": a.get("publish_time"),
         "label": a.get("event_label"), "severity": a.get("severity")}
        for a in ann_items
        if any(kw in (a.get("title") or "") for kw in catalyst_keywords)
    ]
    raw["announcement_catalysts"] = {
        "items": catalyst_items[:5],
        "count": len(catalyst_items),
    }
except Exception as exc:
    logger.warning("[catalyst] announcements failed: %s", exc)

# 从新闻中提取催化线索
try:
    news = ds.search_news(symbol, days=180)
    news_items = _list_of_dicts(news.get("items"))[:20]
    news_catalyst_keywords = ["展会", "峰会", "发布会", "论坛", "大会", "投产",
                               "量产", "签约", "中标", "战略合作", "订单", "招标"]
    news_catalyst_items = [
        {"title": n.get("title"), "source": n.get("source"),
         "time": n.get("publish_time"), "summary": (n.get("summary") or "")[:150]}
        for n in news_items
        if any(kw in (n.get("title") or "") for kw in news_catalyst_keywords)
    ]
    raw["news_catalysts"] = {
        "items": news_catalyst_items[:8],
        "count": len(news_catalyst_items),
    }
except Exception as exc:
    logger.warning("[catalyst] news failed: %s", exc)

# 从研报中提取业绩拐点/技术迭代催化
try:
    research = ds.get_research_report(symbol, days=365)
    research_items = _list_of_dicts(research.get("items"))[:10]
    research_catalyst_keywords = ["拐点", "超预期", "量产", "突破", "新一代",
                                   "发布", "投产", "业绩", "目标价"]
    research_catalyst_items = [
        {"title": r.get("title"), "org": r.get("org"),
         "date": r.get("publish_date"), "rating": r.get("rating"),
         "summary": (r.get("summary") or "")[:150]}
        for r in research_items
        if any(kw in (r.get("title") or "") + (r.get("summary") or "") for kw in research_catalyst_keywords)
    ]
    raw["research_catalysts"] = {
        "items": research_catalyst_items[:5],
        "count": len(research_catalyst_items),
    }
except Exception as exc:
    logger.warning("[catalyst] research failed: %s", exc)
```

**prompt summary 部分**：

```python
lines = [
    "## 公告催化事件",
]
ac = raw.get("announcement_catalysts", {})
if ac.get("items"):
    for item in ac["items"][:5]:
        lines.append(f"- [{item.get('date', '?')}] [{item.get('label', '?')}] {item.get('title', '')[:160]}")
else:
    lines.append(f"- 近90天公告中未找到{ac.get('count', 0)}个催化事件")
lines.extend([
    "",
    "## 新闻催化线索",
])
nc = raw.get("news_catalysts", {})
if nc.get("items"):
    for item in nc["items"][:8]:
        lines.append(f"- [{item.get('time', '?')}] {item.get('source', '?')}：{item.get('title', '')[:140]}；{item.get('summary', '')}")
else:
    lines.append(f"- 近180天新闻中未找到{nc.get('count', 0)}条催化线索")
lines.extend([
    "",
    "## 研报催化线索",
])
rc = raw.get("research_catalysts", {})
if rc.get("items"):
    for item in rc["items"][:5]:
        lines.append(f"- [{item.get('date', '?')}] {item.get('org', '?')} [{item.get('rating', '?')}]：{item.get('title', '')[:120]}；{item.get('summary', '')}")
else:
    lines.append(f"- 研报中未找到{rc.get('count', 0)}条催化线索")
lines.extend([
    "",
    "## 判断约束",
    "- 关注未来 6-12 个月内可预见的催化事件（如已知展会、政策窗口、业绩拐点、技术迭代）。",
    "- 已完全消化的事件（利好出尽）不算有效催化。",
    "- 至少一个具体催化才判为通过。",
])
summary = "\n".join(lines)
```

- [ ] **Step 1: 完全重写 catalyst_events.py collect_data 方法**
  - 删除 `get_catalyst_data()` 调用
  - 实现从公告/新闻/研报三类原始数据中提取催化事件
  - 重写 prompt summary 构建

- [ ] **Step 2: 运行测试验证**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m pytest tests/test_buy_criteria.py -v -k catalyst 2>&1 | tail -30
```

- [ ] **Step 3: 编译检查**

```bash
python3 -m compileall -q src/services/buy_criteria/evaluators/catalyst_events.py
```

---

## Task 6: 更新 DataService — 添加缺失的 raw data 方法

**Files:**
- Modify: `src/services/buy_criteria/data_service.py`
- Test: `tests/test_buy_criteria.py`

部分评估器需要的原始数据方法在 DataService 中缺失或不存在：

- `get_announcements(symbol, days)` — 获取公告（部分 evaluator 需要）
- `get_sector_list` 已存在
- `get_sector_flow_industry` 已存在

- [ ] **Step 1: 检查 DataService 是否需要新增方法**

当前 `data_service.py` 已有：
- `get_sector_list("industry")` — ✅ 板块排名
- `get_sector_flow_industry()` — ✅ 资金流
- `search_news(symbol, days)` — ✅ 新闻搜索
- `get_research_report(symbol, days)` — ✅ 研报
- `get_risk_events(symbol, days)` — ✅ 风险事件/公告

**结论**：DataService 已有所有需要的原始数据方法，无需新增。

- [ ] **Step 2: 移除不再需要的 `get_industry_cycle_report` 方法**

```python
# 删除第62-64行
def get_industry_cycle_report(self, symbol: str) -> dict[str, Any]:
    from src.services.industry_cycle_service import IndustryCycleService
    return IndustryCycleService().get_report(symbol=symbol)
```

⚠️ **注意**：先确认 `IndustryCycleService` 没有其他调用者再删除。检查 grep 结果后确认只有 buy_criteria evaluators 在调用它，删除是安全的。

---

## Task 7: 集成测试

**Files:**
- Test: `tests/test_buy_criteria.py`

- [ ] **Step 1: 编译所有修改后的文件**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m compileall -q src/services/buy_criteria/evaluators/prosperity_cycle.py
python3 -m compileall -q src/services/buy_criteria/evaluators/growth_space.py
python3 -m compileall -q src/services/buy_criteria/evaluators/growth_drivers.py
python3 -m compileall -q src/services/buy_criteria/evaluators/competition_landscape.py
python3 -m compileall -q src/services/buy_criteria/evaluators/catalyst_events.py
python3 -m compileall -q src/services/buy_criteria/data_service.py
```

Expected: 所有文件编译通过，无错误

- [ ] **Step 2: 运行所有买入判断测试**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
python3 -m pytest tests/test_buy_criteria.py -v 2>&1 | tail -50
```

Expected: 所有测试通过

- [ ] **Step 3: 手动验证（选一支股票跑完整的买入判断流）**

通过 API 或前端跑一个实际的 symbol（如 300502），确认：
1. 8 个评估器都能独立拿到原始数据
2. prompt 中不再出现 LLM 生成的 `prosperity_score` 等预设结论
3. SSE 流正常输出

---

## Task 8: 清理 IndustryCycleService 的调用链（可选但推荐）

**Files:**
- Modify: `src/services/buy_criteria/data_service.py`
- Consider: `api/v1/endpoints/industry_cycle.py`

- [ ] **Step 1: 确认 `IndustryCycleService` 的唯一调用者**

```bash
grep -rn "IndustryCycleService\|get_industry_cycle_report\|industry_cycle_report" --include="*.py" | grep -v __pycache__ | grep -v test
```

如果结果只剩下：
- `api/v1/endpoints/industry_cycle.py`（独立 API 端点，保留）
- `src/services/industry_cycle_service.py`（自身定义，保留）
- `src/services/buy_criteria/` 中已删除的调用

则 buy_criteria 已完全解耦。

- [ ] **Step 2: 确认独立 API 端点不受影响**

`api/v1/endpoints/industry_cycle.py` 的 3 个端点仍然工作正常，`IndustryCycleService` 继续为前端独立使用场景服务（如果有的话）。

---

## 验收标准

完成所有任务后，验证以下 8 个准则各自独立：

| # | 准则 | 数据来源 | 不再依赖 |
|---|------|---------|---------|
| ① | 市场主线 | `MarketThemeService` 原始主线报告 | — |
| ② | 景气上行周期 | 板块排名 + 资金流 + 财务增速 + PMI | `IndustryCycleService` |
| ③ | 未来3年空间 | 研报预测 + 财务增长 + 新闻线索 | `IndustryCycleService` |
|  | 竞争格局 | 毛利率 + 板块排名 + 价格战搜索 | `IndustryCycleService` |
| ⑤ | 驱动因素 | 政策新闻 + 技术研报 + 需求新闻 | `IndustryCycleService` |
| ⑥ | 催化事件 | 公告 + 新闻 + 研报提取 | 不存在的端点 |
| ⑦ | 估值水位 | `get_valuation_ratios` 原始数据 | — |
| ⑧ | 致命风险 | 风险事件 + 股东结构 + 财务信号 | — |

前端"查看模型输入"中，不再看到预设的"行业景气度评分：72"之类的 LLM 结论。
