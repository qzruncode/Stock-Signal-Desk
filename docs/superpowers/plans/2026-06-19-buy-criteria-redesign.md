# 买入分析页面重构 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 6-step BuyDecisionWorkbench with 8 independent criterion evaluators that run sequentially with early termination, each using data collection + LLM judgment.

**Architecture:** Backend uses `CriterionOrchestrator` to run 8 evaluator classes in order. Each evaluator collects data via `DataService` (wrapper over existing endpoint functions), then calls LLM via `call_ai_structured` for a pass/fail verdict. Results stream to frontend via SSE using the existing Pattern B (threading worker + asyncio.Queue + StreamingResponse). Frontend renders 8 flat cards with LLM verdicts and collapsible raw data.

**Tech Stack:** FastAPI, Pydantic v2, LiteLLM (via `call_ai_structured`), React + Tailwind CSS, `lucide-react` icons, `cn()` utility, Axios.

---

## File Structure

### Backend — New Files

| File | Responsibility |
|---|---|
| `api/v1/schemas/buy_criteria.py` | Pydantic request/response models |
| `src/services/buy_criteria/__init__.py` | Package init, exports |
| `src/services/buy_criteria/base.py` | `BaseCriterionEvaluator` ABC + `CriterionResult` dataclass |
| `src/services/buy_criteria/data_service.py` | `DataService` — wraps existing endpoint functions |
| ~~`src/services/buy_criteria/llm_utils.py`~~ | ~~JSON parsing lives in `base.py` — no separate file needed~~ |
| `src/services/buy_criteria/prompts/__init__.py` | Package init |
| `src/services/buy_criteria/prompts/rubrics.py` | 8 criterion rubric strings |
| `src/services/buy_criteria/evaluators/__init__.py` | Package init, registry list |
| `src/services/buy_criteria/evaluators/mainline_position.py` | ① 市场主线属性 |
| `src/services/buy_criteria/evaluators/prosperity_cycle.py` | ② 景气上行周期 |
| `src/services/buy_criteria/evaluators/growth_space.py` | ③ 未来3年空间 |
| `src/services/buy_criteria/evaluators/competition_landscape.py` | ④ 竞争格局 |
| `src/services/buy_criteria/evaluators/growth_drivers.py` | ⑤ 驱动因素 |
| `src/services/buy_criteria/evaluators/catalyst_events.py` | ⑥ 催化事件 |
| `src/services/buy_criteria/evaluators/valuation_level.py` | ⑦ 估值水位 |
| `src/services/buy_criteria/evaluators/fatal_risks.py` | ⑧ 致命风险 |
| `src/services/buy_criteria/orchestrator.py` | `CriterionOrchestrator` — sequential execution + SSE generator |
| `tests/test_buy_criteria.py` | Backend tests |

### Frontend — New Files

| File | Responsibility |
|---|---|
| `apps/dsa-web/src/api/buyCriteria.ts` | API client + TypeScript types |
| `apps/dsa-web/src/hooks/useBuyCriteria.ts` | SSE hook + state management |
| `apps/dsa-web/src/components/buyCriteria/CriterionCard.tsx` | Single criterion card (5 states) |
| `apps/dsa-web/src/components/buyCriteria/SummaryBar.tsx` | Top summary bar |
| `apps/dsa-web/src/components/buyCriteria/BuyCriteriaPanel.tsx` | Tab root component |

### Backend — Modified Files

| File | Change |
|---|---|
| `api/v1/endpoints/buy_decision.py` | Add new criteria routes |
| `api/v1/router.py` | No change (already includes buy_decision router) |

### Frontend — Modified Files

| File | Change |
|---|---|
| `apps/dsa-web/src/pages/StockAnalysisPage.tsx` | Replace `BuyDecisionWorkbench` import + usage with `BuyCriteriaPanel` |

### Files to Delete (after new code works)

| File | Reason |
|---|---|
| `apps/dsa-web/src/components/buyDecision/BuyDecisionWorkbench.tsx` | Replaced by BuyCriteriaPanel |
| `apps/dsa-web/src/components/buyDecision/IndustryBetaChat.tsx` | Industry analysis now in evaluators |
| `apps/dsa-web/src/hooks/useBuyDecisionWorkbench.ts` | Replaced by useBuyCriteria |
| `apps/dsa-web/src/api/buyDecisionWorkbench.ts` | Replaced by buyCriteria.ts |
| `src/services/buy_decision_workbench_service.py` | Replaced by buy_criteria/ |
| `tests/test_buy_decision_api.py` | Replaced by test_buy_criteria.py |

---

## Task 1: Backend Pydantic Schemas

**Files:**
- Create: `api/v1/schemas/buy_criteria.py`

- [ ] **Step 1: Create the schemas file**

```python
# api/v1/schemas/buy_criteria.py
"""Pydantic models for buy criteria analysis."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CriteriaAnalyzeRequest(BaseModel):
    """Request body for starting a criteria analysis."""
    symbol: str = Field(..., description="股票代码", example="300308")


class CriterionEvidence(BaseModel):
    """Evidence collected for a single criterion."""
    raw_data: dict[str, Any] = Field(default_factory=dict, description="采集的原始数据")
    data_summary: str = Field(default="", description="数据摘要（给人看的）")


class CriterionResultPayload(BaseModel):
    """Result of a single criterion evaluation."""
    criterion_id: str = Field(..., description="准则ID", example="mainline_position")
    criterion_name: str = Field(..., description="准则名称", example="市场主线属性")
    index: int = Field(..., ge=0, le=7, description="准则序号 0-7")
    passed: bool = Field(..., description="是否通过")
    verdict: str = Field(..., description="LLM 2-3句话定性判断")
    evidence: CriterionEvidence = Field(default_factory=CriterionEvidence)
    analyzed_at: str = Field(..., description="分析时间 ISO timestamp")


class AnalysisCompletePayload(BaseModel):
    """Final summary when analysis completes."""
    final_decision: str = Field(..., description="可买入 | 不可买入")
    passed_count: int = Field(..., ge=0, le=8)
    failed_count: int = Field(..., ge=0, le=8)
    not_evaluated_count: int = Field(..., ge=0, le=8)
    stopped_at: Optional[str] = Field(None, description="导致终止的 criterion_id")
    summary: str = Field(default="", description="一句话总结")


class CriterionStartPayload(BaseModel):
    """Emitted when a criterion starts evaluation."""
    criterion_id: str
    criterion_name: str
    index: int


class ErrorPayload(BaseModel):
    """Emitted when a criterion encounters an error."""
    criterion_id: str
    message: str
```

- [ ] **Step 2: Verify the file compiles**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from api.v1.schemas.buy_criteria import CriteriaAnalyzeRequest, CriterionResultPayload; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/v1/schemas/buy_criteria.py
git commit -m "feat(buy-criteria): add Pydantic schemas for criteria analysis"
```

---

## Task 2: Base Evaluator + Result Dataclass

**Files:**
- Create: `src/services/buy_criteria/__init__.py`
- Create: `src/services/buy_criteria/base.py`

- [ ] **Step 1: Create package init**

```python
# src/services/buy_criteria/__init__.py
"""Buy criteria analysis — 8 independent evaluators with sequential execution."""
```

- [ ] **Step 2: Create base evaluator**

```python
# src/services/buy_criteria/base.py
"""Abstract base class for criterion evaluators."""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CriterionEvidence:
    """Evidence collected for a criterion."""
    raw_data: dict[str, Any] = field(default_factory=dict)
    data_summary: str = ""


@dataclass
class CriterionResult:
    """Result of a single criterion evaluation."""
    criterion_id: str
    criterion_name: str
    index: int
    passed: bool
    verdict: str
    evidence: CriterionEvidence = field(default_factory=CriterionEvidence)
    analyzed_at: str = ""

    def __post_init__(self):
        if not self.analyzed_at:
            self.analyzed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "criterion_name": self.criterion_name,
            "index": self.index,
            "passed": self.passed,
            "verdict": self.verdict,
            "evidence": {
                "raw_data": self.evidence.raw_data,
                "data_summary": self.evidence.data_summary,
            },
            "analyzed_at": self.analyzed_at,
        }


class BaseCriterionEvaluator(ABC):
    """Abstract base class for all 8 criterion evaluators."""

    criterion_id: str = ""
    criterion_name: str = ""
    index: int = -1

    @abstractmethod
    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        """Collect data needed for this criterion. Must not call LLM."""
        ...

    @abstractmethod
    def get_rubric(self) -> str:
        """Return the judgment rubric text for the LLM prompt."""
        ...

    def build_user_prompt(self, stock_info: dict[str, Any], evidence: CriterionEvidence) -> str:
        """Build the user prompt. Override for custom structure."""
        symbol = stock_info.get("symbol", symbol if isinstance(symbol, str) else "")
        stock_name = stock_info.get("name", stock_info.get("short_name", ""))
        industry = stock_info.get("industry", "")
        return (
            f"你是一个A股行业分析师。请基于以下数据，判断【{self.criterion_name}】是否满足条件。\n\n"
            f"## 判定标准\n{self.get_rubric()}\n\n"
            f"## 股票信息\n股票: {stock_name} ({symbol})\n行业: {industry}\n\n"
            f"## 数据\n{evidence.data_summary}\n\n"
            f'## 请返回 JSON\n{{"passed": true/false, "verdict": "2-3句话的定性判断"}}'
        )

    def evaluate(self, symbol: str, stock_info: dict[str, Any]) -> CriterionResult:
        """Full evaluation: collect_data → LLM → result. Handles retry."""
        evidence = self.collect_data(symbol, stock_info)
        user_prompt = self.build_user_prompt(stock_info, evidence)

        result = self._call_llm(user_prompt, attempt=0)
        if result is None:
            # Retry once
            result = self._call_llm(user_prompt, attempt=1)

        if result is None:
            return CriterionResult(
                criterion_id=self.criterion_id,
                criterion_name=self.criterion_name,
                index=self.index,
                passed=False,
                verdict=f"{self.criterion_name}评估失败：LLM 无法返回有效判断。",
                evidence=evidence,
            )

        passed = result.get("passed", False)
        if isinstance(passed, str):
            passed = passed.lower() in ("true", "yes", "是")
        verdict = str(result.get("verdict", ""))

        return CriterionResult(
            criterion_id=self.criterion_id,
            criterion_name=self.criterion_name,
            index=self.index,
            passed=bool(passed),
            verdict=verdict,
            evidence=evidence,
        )

    def _call_llm(self, user_prompt: str, *, attempt: int) -> Optional[dict[str, Any]]:
        """Call LLM and parse JSON response. Returns None on failure."""
        from src.ai_caller import call_ai_structured
        from src.analyzer import get_analyzer
        from src.storage import persist_llm_usage

        system_prompt = "你是一个专业的A股行业分析师。请严格按照判定标准进行分析，并以JSON格式返回结果。"
        try:
            analyzer = get_analyzer()
            response_text, model_used, usage = call_ai_structured(
                analyzer,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                call_type=f"buy_criteria_{self.criterion_id}",
                temperature=0.2,
                max_tokens=2048,
            )
            persist_llm_usage(usage, model_used, f"buy_criteria_{self.criterion_id}")
            return _parse_verdict_json(response_text)
        except Exception as exc:
            logger.warning(
                "[buy_criteria] LLM call failed for %s (attempt %d): %s",
                self.criterion_id, attempt, exc,
            )
            return None


def _parse_verdict_json(raw_text: str) -> Optional[dict[str, Any]]:
    """Extract a JSON dict from LLM output, with repair strategies."""
    text = raw_text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    candidates = [text]

    # Try extracting first {...} block
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        extracted = match.group(0)
        if extracted not in candidates:
            candidates.append(extracted)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    return None
```

- [ ] **Step 3: Verify import**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionResult; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/services/buy_criteria/__init__.py src/services/buy_criteria/base.py
git commit -m "feat(buy-criteria): add BaseCriterionEvaluator with LLM retry and JSON parsing"
```

---

## Task 3: DataService — Unified Data Fetching Wrapper

**Files:**
- Create: `src/services/buy_criteria/data_service.py`

- [ ] **Step 1: Create DataService**

```python
# src/services/buy_criteria/data_service.py
"""Unified data-fetching wrapper for criterion evaluators.

Wraps existing endpoint functions so evaluators don't import endpoints directly.
All functions return plain dicts and use per-day SQLite caching upstream.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DataService:
    """Provides data to evaluators by wrapping existing endpoint functions."""

    def get_stock_info(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.stock_info import get_stock_info
        return get_stock_info(symbol)

    def get_sector_list(self, sector_type: str = "industry") -> dict[str, Any]:
        from api.v1.endpoints.sectors import get_sector_list
        return get_sector_list(type=sector_type)

    def get_valuation_ratios(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_valuation_ratios
        return get_valuation_ratios(symbol, with_history=True)

    def get_price_overdraft_signal(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_price_overdraft_signal
        return get_price_overdraft_signal(symbol)

    def get_shareholder_structure(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_shareholder_structure
        return get_shareholder_structure(symbol)

    def get_sentiment(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_sentiment
        return get_sentiment(symbol, days=days)

    def get_social_sentiment(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_social_sentiment
        return get_social_sentiment(symbol, days=days)

    def get_risk_events(self, symbol: str, days: int = 90) -> dict[str, Any]:
        from api.v1.endpoints.financials import get_risk_events
        return get_risk_events(symbol, days=days)

    def get_sector_flow_industry(self, symbol: str) -> dict[str, Any]:
        from api.v1.endpoints.macro import _fetch_sector_flow_industry
        return _fetch_sector_flow_industry(symbol)

    def get_industry_cycle_report(self, symbol: str) -> dict[str, Any]:
        from src.services.industry_cycle_service import IndustryCycleService
        return IndustryCycleService().get_report(symbol=symbol)

    def get_catalyst_data(self, symbol: str) -> dict[str, Any]:
        """Get catalyst data — wraps the catalyst analysis endpoint."""
        try:
            from api.v1.endpoints.catalyst import get_catalyst_analysis
            return get_catalyst_analysis(symbol)
        except (ImportError, AttributeError):
            logger.warning("[DataService] catalyst endpoint not available")
            return {}
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.data_service import DataService; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/services/buy_criteria/data_service.py
git commit -m "feat(buy-criteria): add DataService wrapper over existing endpoint functions"
```

---

## Task 4: Criterion Rubrics (LLM Prompt Text)

**Files:**
- Create: `src/services/buy_criteria/prompts/__init__.py`
- Create: `src/services/buy_criteria/prompts/rubrics.py`

- [ ] **Step 1: Create prompts package init**

```python
# src/services/buy_criteria/prompts/__init__.py
```

- [ ] **Step 2: Create rubrics file**

```python
# src/services/buy_criteria/prompts/rubrics.py
"""Judgment rubric text for each of the 8 criteria.

Each rubric is embedded in the LLM prompt as the "判定标准" section.
"""

MAINLINE_POSITION = """\
判断该股票所属行业当前是否属于市场主线或分支主线。

通过条件（满足任一）：
- 行业板块资金净流入排名前10
- 近30天有明确的国家/部委级别政策支持
- 行业成交额占全市场比例持续上升
- 机构调研/持仓集中度显著提升

不通过条件：
- 行业资金持续净流出
- 无任何政策提及或市场关注
- 板块热度过低，成交额占比下降

注意区分：核心主线（强通过）vs 分支主线（通过）vs 非主线（不通过）"""

PROSPERITY_CYCLE = """\
判断该行业是否处于上升周期（景气上行）。

通过条件（满足任一）：
- 行业景气度评分 ≥ 65（满分100）
- 近3个季度营收增速逐季加速或维持高位（≥15%）
- PMI相关细分指标处于扩张区间（>50）
- 产能利用率环比提升

不通过条件：
- 景气度评分 < 50
- 营收增速连续2个季度下滑
- 行业处于明显的下行周期

注意区分：上行期（强通过）vs 底部复苏（通过）vs 顶部/下行期（不通过）"""

GROWTH_SPACE = """\
判断该行业未来3年是否有明确的增长空间（增量市场 vs 存量博弈）。

通过条件（满足任一）：
- 行业CAGR预测 ≥ 15%
- 核心产品/服务渗透率 < 40%（仍有大量提升空间）
- 有明确的新增需求来源（新市场/新技术替代/政策创造的新需求）
- 市场规模预测显示持续扩大

不通过条件：
- 行业增速 < 5%（低增长/零和博弈）
- 渗透率已 > 70%（天花板逼近）
- 增长主要靠抢占竞争对手份额而非新增需求
- 市场空间主要依赖存量替换"""

COMPETITION_LANDSCAPE = """\
判断该行业是否属于价格战/内卷行业。

通过条件（行业竞争格局健康）：
- 行业平均毛利率稳定或提升
- CR5 ≥ 50%（集中度合理，头部有定价权）
- 头部企业之间差异化明显，非纯价格竞争
- 新进入者壁垒较高

不通过条件（内卷/价格战）：
- 行业平均毛利率连续2个季度下滑
- CR5 < 30%（过度分散，恶性竞争）
- 出现明显的降价/促销战信号
- 产品高度同质化，竞争主要靠价格"""

GROWTH_DRIVERS = """\
判断该行业是否有明确且可持续的增长驱动因素（政策/技术/需求至少一种）。

通过条件（至少一种驱动力明确）：
- 政策驱动：近6个月有国家级/部委级产业政策出台，且政策方向明确利好
- 技术驱动：有重大技术突破或迭代，推动行业效率提升或创造新需求
- 需求驱动：下游需求数据（订单/出货量/装机量）持续增长

不通过条件：
- 无任何明确驱动力，行业增长缺乏支撑
- 纯概念炒作（有题材但无实质政策/技术/需求落地）
- 驱动力已过高峰，正在衰减"""

CATALYST_EVENTS = """\
判断未来6-12个月是否有可预见的催化事件。

通过条件（至少一个具体催化）：
- 已知的行业展会/峰会/产品发布会在未来6个月内
- 重要政策窗口期（如两会/产业规划发布/补贴到期前的抢装）
- 公司业绩拐点预期（如下季度财报有望超预期）
- 技术迭代节点（如新一代产品量产/商用）

不通过条件：
- 未来6-12个月无任何可预见的催化事件
- 催化事件已完全被市场消化（利好出尽）
- 催化事件时间不确定且概率较低"""

VALUATION_LEVEL = """\
判断当前估值是否已经透支了未来增长预期。

通过条件（估值合理或未高估）：
- PE历史分位数 < 70%
- PEG < 1.5
- 估值低于或接近行业平均水平
- 股价透支信号为"未透支"或"低透支"

不通过条件（估值透支）：
- PE历史分位数 ≥ 85% 且 PEG > 2.0
- 估值显著高于行业平均（>1.5倍）
- 股价透支信号为"高度透支"
- 当前估值已反映乐观预期，无安全边际"""

FATAL_RISKS = """\
扫描是否存在尚未被市场充分定价的致命风险。

通过条件（无致命风险）：
- 无重大财务异常（商誉/应收账款/现金流无严重问题）
- 无重大监管风险或政策打压信号
- 大股东无大规模质押或减持计划
- 无技术路线被颠覆的风险

不通过条件（存在致命风险）：
- 财务造假迹象（如应收异常增长、现金流与利润严重背离）
- 重大监管变化（如行业面临禁令/严格限制）
- 大股东高比例质押（>50%）且持续减持
- 核心技术/产品面临颠覆性替代
- 重大诉讼/违规事件"""

# Ordered list matching evaluator execution order
RUBRICS_BY_ID: dict[str, str] = {
    "mainline_position": MAINLINE_POSITION,
    "prosperity_cycle": PROSPERITY_CYCLE,
    "growth_space": GROWTH_SPACE,
    "competition_landscape": COMPETITION_LANDSCAPE,
    "growth_drivers": GROWTH_DRIVERS,
    "catalyst_events": CATALYST_EVENTS,
    "valuation_level": VALUATION_LEVEL,
    "fatal_risks": FATAL_RISKS,
}
```

- [ ] **Step 3: Verify import**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.prompts.rubrics import RUBRICS_BY_ID; print(f'{len(RUBRICS_BY_ID)} rubrics OK')"`
Expected: `8 rubrics OK`

- [ ] **Step 4: Commit**

```bash
git add src/services/buy_criteria/prompts/__init__.py src/services/buy_criteria/prompts/rubrics.py
git commit -m "feat(buy-criteria): add 8 criterion rubrics for LLM prompts"
```

---

## Task 5: Evaluators ①② (Mainline + Prosperity)

**Files:**
- Create: `src/services/buy_criteria/evaluators/__init__.py`
- Create: `src/services/buy_criteria/evaluators/mainline_position.py`
- Create: `src/services/buy_criteria/evaluators/prosperity_cycle.py`

- [ ] **Step 1: Create evaluators package init**

```python
# src/services/buy_criteria/evaluators/__init__.py
"""Criterion evaluator implementations."""
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator
from src.services.buy_criteria.evaluators.growth_space import GrowthSpaceEvaluator
from src.services.buy_criteria.evaluators.competition_landscape import CompetitionLandscapeEvaluator
from src.services.buy_criteria.evaluators.growth_drivers import GrowthDriversEvaluator
from src.services.buy_criteria.evaluators.catalyst_events import CatalystEventsEvaluator
from src.services.buy_criteria.evaluators.valuation_level import ValuationLevelEvaluator
from src.services.buy_criteria.evaluators.fatal_risks import FatalRisksEvaluator

# Ordered list — determines execution sequence
EVALUATOR_CLASSES = [
    MainlinePositionEvaluator,
    ProsperityCycleEvaluator,
    GrowthSpaceEvaluator,
    CompetitionLandscapeEvaluator,
    GrowthDriversEvaluator,
    CatalystEventsEvaluator,
    ValuationLevelEvaluator,
    FatalRisksEvaluator,
]
```

- [ ] **Step 2: Create evaluator ① — 市场主线属性**

```python
# src/services/buy_criteria/evaluators/mainline_position.py
"""Evaluator ①: 市场主线属性 — Is the stock's industry a market mainline?"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import MAINLINE_POSITION

logger = logging.getLogger(__name__)


class MainlinePositionEvaluator(BaseCriterionEvaluator):
    criterion_id = "mainline_position"
    criterion_name = "市场主线属性"
    index = 0

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Sector fund flow ranking
        try:
            sectors = ds.get_sector_list("industry")
            raw["sector_list"] = {
                "top_10": [
                    {"name": s["name"], "change_pct": s.get("change_pct"), "rank": s.get("rank"), "total_amount": s.get("total_amount")}
                    for s in (sectors.get("items") or [])[:10]
                ],
                "data_time": sectors.get("data_time"),
            }
            # Find this stock's industry in the ranking
            industry = stock_info.get("industry", "")
            for s in (sectors.get("items") or []):
                if industry and s.get("name") == industry:
                    raw["target_industry_rank"] = {"name": s["name"], "rank": s.get("rank"), "change_pct": s.get("change_pct"), "total_amount": s.get("total_amount")}
                    break
        except Exception as exc:
            logger.warning("[mainline] sector_list failed: %s", exc)
            raw["sector_list_error"] = str(exc)

        # Sentiment
        try:
            sentiment = ds.get_sentiment(symbol)
            raw["sentiment"] = {
                "score": sentiment.get("sentiment_score"),
                "total_discussion": sentiment.get("total_discussion"),
            }
        except Exception as exc:
            logger.warning("[mainline] sentiment failed: %s", exc)
            raw["sentiment-error"] = str(exc)

        # Social sentiment
        try:
            social = ds.get_social_sentiment(symbol)
            raw["social_sentiment"] = {
                "score": social.get("score") or social.get("social_score"),
                "trend": social.get("trend"),
            }
        except Exception as exc:
            logger.warning("[mainline] social_sentiment failed: %s", exc)

        # Build summary
        parts = []
        if "target_industry_rank" in raw:
            r = raw["target_industry_rank"]
            parts.append(f"行业[{r['name']}]在板块排名中位列第{r.get('rank', '?')}名，涨跌幅{r.get('change_pct', '?')}%")
        if raw.get("sentiment", {}).get("score") is not None:
            parts.append(f"舆情情绪评分{raw['sentiment']['score']}，总讨论量{raw.get('sentiment', {}).get('total_discussion', '?')}")
        if raw.get("social_sentiment", {}).get("score") is not None:
            parts.append(f"社交情绪评分{raw['social_sentiment']['score']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return MAINLINE_POSITION
```

- [ ] **Step 3: Create evaluator ② — 景气上行周期**

```python
# src/services/buy_criteria/evaluators/prosperity_cycle.py
"""Evaluator ②: 景气上行周期 — Is the industry in an upward cycle?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import PROSPERITY_CYCLE

logger = logging.getLogger(__name__)


class ProsperityCycleEvaluator(BaseCriterionEvaluator):
    criterion_id = "prosperity_cycle"
    criterion_name = "景气上行周期"
    index = 1

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has prosperity score, cycle phase)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "prosperity_score": report.get("prosperity_score"),
                "cycle_phase": report.get("cycle_phase"),
                "core_logic": report.get("core_logic"),
                "analysis_status": report.get("analysis_status"),
            }
        except Exception as exc:
            logger.warning("[prosperity] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Valuation ratios (has revenue trend data)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["valuation"] = {
                "pe_ttm": valuation.get("pe_ttm"),
                "pb": valuation.get("pb"),
                "industry_average": valuation.get("industry_average"),
                "revenue_growth": valuation.get("revenue_growth") or valuation.get("revenue_yoy"),
            }
        except Exception as exc:
            logger.warning("[prosperity] valuation failed: %s", exc)

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("prosperity_score") is not None:
            parts.append(f"行业景气度评分{ic['prosperity_score']}分")
        if ic.get("cycle_phase"):
            parts.append(f"周期阶段：{ic['cycle_phase']}")
        if ic.get("core_logic"):
            parts.append(f"核心逻辑：{ic['core_logic']}")
        v = raw.get("valuation", {})
        if v.get("revenue_growth") is not None:
            parts.append(f"营收增速：{v['revenue_growth']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return PROSPERITY_CYCLE
```

- [ ] **Step 4: Verify imports**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator; from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add src/services/buy_criteria/evaluators/__init__.py src/services/buy_criteria/evaluators/mainline_position.py src/services/buy_criteria/evaluators/prosperity_cycle.py
git commit -m "feat(buy-criteria): add evaluators 1-2 (mainline position + prosperity cycle)"
```

---

## Task 6: Evaluators ③④ (Growth Space + Competition)

**Files:**
- Create: `src/services/buy_criteria/evaluators/growth_space.py`
- Create: `src/services/buy_criteria/evaluators/competition_landscape.py`

- [ ] **Step 1: Create evaluator ③ — 未来3年空间**

```python
# src/services/buy_criteria/evaluators/growth_space.py
"""Evaluator ③: 未来3年空间 — Is there clear 3-year growth space (not zero-sum)?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_SPACE

logger = logging.getLogger(__name__)


class GrowthSpaceEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_space"
    criterion_name = "未来3年空间"
    index = 2

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has market size, penetration, CAGR data)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "market_size_forecast": report.get("market_size_forecast") or report.get("market_size"),
                "penetration_rate": report.get("penetration_rate") or report.get("penetration"),
                "cagr_forecast": report.get("cagr") or report.get("cagr_forecast"),
                "growth_drivers": report.get("growth_drivers"),
                "new_demand_sources": report.get("new_demand_sources"),
                "core_logic": report.get("core_logic"),
            }
        except Exception as exc:
            logger.warning("[growth_space] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Stock info (for industry context)
        raw["stock_info"] = {
            "industry": stock_info.get("industry", ""),
            "main_business": stock_info.get("main_business", ""),
        }

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("cagr_forecast"):
            parts.append(f"行业CAGR预测：{ic['cagr_forecast']}")
        if ic.get("penetration_rate") is not None:
            parts.append(f"当前渗透率：{ic['penetration_rate']}")
        if ic.get("market_size_forecast"):
            parts.append(f"市场规模预测：{ic['market_size_forecast']}")
        if ic.get("new_demand_sources"):
            parts.append(f"新增需求来源：{ic['new_demand_sources']}")
        if ic.get("core_logic"):
            parts.append(f"核心逻辑：{ic['core_logic']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_SPACE
```

- [ ] **Step 2: Create evaluator ④ — 竞争格局**

```python
# src/services/buy_criteria/evaluators/competition_landscape.py
"""Evaluator ④: 竞争格局 — Is the industry free from price wars/involution?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import COMPETITION_LANDSCAPE

logger = logging.getLogger(__name__)


class CompetitionLandscapeEvaluator(BaseCriterionEvaluator):
    criterion_id = "competition_landscape"
    criterion_name = "竞争格局"
    index = 3

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has concentration, margin data)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "concentration_cr5": report.get("concentration_cr5") or report.get("cr5"),
                "concentration_hhi": report.get("hhi"),
                "gross_margin_trend": report.get("gross_margin_trend") or report.get("margin_trend"),
                "competition_intensity": report.get("competition_intensity"),
                "price_war_signals": report.get("price_war_signals"),
                "peer_comparison": report.get("peer_comparison"),
            }
        except Exception as exc:
            logger.warning("[competition] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Valuation (for margin data)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["valuation"] = {
                "gross_margin": valuation.get("gross_margin"),
                "net_margin": valuation.get("net_margin"),
                "industry_average": valuation.get("industry_average", {}),
            }
        except Exception as exc:
            logger.warning("[competition] valuation failed: %s", exc)

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("concentration_cr5") is not None:
            parts.append(f"行业CR5：{ic['concentration_cr5']}%")
        if ic.get("gross_margin_trend"):
            parts.append(f"毛利率趋势：{ic['gross_margin_trend']}")
        if ic.get("competition_intensity"):
            parts.append(f"竞争烈度：{ic['competition_intensity']}")
        if ic.get("price_war_signals"):
            parts.append(f"价格战信号：{ic['price_war_signals']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return COMPETITION_LANDSCAPE
```

- [ ] **Step 3: Verify imports**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.evaluators.growth_space import GrowthSpaceEvaluator; from src.services.buy_criteria.evaluators.competition_landscape import CompetitionLandscapeEvaluator; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/services/buy_criteria/evaluators/growth_space.py src/services/buy_criteria/evaluators/competition_landscape.py
git commit -m "feat(buy-criteria): add evaluators 3-4 (growth space + competition landscape)"
```

---

## Task 7: Evaluators ⑤⑥ (Growth Drivers + Catalyst Events)

**Files:**
- Create: `src/services/buy_criteria/evaluators/growth_drivers.py`
- Create: `src/services/buy_criteria/evaluators/catalyst_events.py`

- [ ] **Step 1: Create evaluator ⑤ — 驱动因素**

```python
# src/services/buy_criteria/evaluators/growth_drivers.py
"""Evaluator ⑤: 驱动因素 — Does the industry have policy/tech/demand drivers?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import GROWTH_DRIVERS

logger = logging.getLogger(__name__)


class GrowthDriversEvaluator(BaseCriterionEvaluator):
    criterion_id = "growth_drivers"
    criterion_name = "驱动因素"
    index = 4

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Industry cycle report (has driver analysis)
        try:
            report = ds.get_industry_cycle_report(symbol)
            raw["industry_cycle"] = {
                "policy_drivers": report.get("policy_drivers") or report.get("policy_factors"),
                "tech_drivers": report.get("tech_drivers") or report.get("technology_factors"),
                "demand_drivers": report.get("demand_drivers") or report.get("demand_factors"),
                "growth_drivers": report.get("growth_drivers"),
                "core_logic": report.get("core_logic"),
            }
        except Exception as exc:
            logger.warning("[drivers] industry_cycle failed: %s", exc)
            raw["industry_cycle_error"] = str(exc)

        # Sentiment (for market attention to drivers)
        try:
            sentiment = ds.get_sentiment(symbol)
            items = sentiment.get("items") or []
            raw["sentiment"] = {
                "top_items": [{"title": i.get("title"), "score": i.get("score")} for i in items[:5]],
                "research_items_count": len(sentiment.get("research_items") or []),
            }
        except Exception as exc:
            logger.warning("[drivers] sentiment failed: %s", exc)

        # Build summary
        parts = []
        ic = raw.get("industry_cycle", {})
        if ic.get("policy_drivers"):
            parts.append(f"政策驱动：{ic['policy_drivers']}")
        if ic.get("tech_drivers"):
            parts.append(f"技术驱动：{ic['tech_drivers']}")
        if ic.get("demand_drivers"):
            parts.append(f"需求驱动：{ic['demand_drivers']}")
        if ic.get("growth_drivers"):
            parts.append(f"增长驱动：{ic['growth_drivers']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return GROWTH_DRIVERS
```

- [ ] **Step 2: Create evaluator ⑥ — 催化事件**

```python
# src/services/buy_criteria/evaluators/catalyst_events.py
"""Evaluator ⑥: 催化事件 — Are there catalysts in the next 6-12 months?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import CATALYST_EVENTS

logger = logging.getLogger(__name__)


class CatalystEventsEvaluator(BaseCriterionEvaluator):
    criterion_id = "catalyst_events"
    criterion_name = "催化事件"
    index = 5

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Catalyst analysis data
        try:
            catalyst = ds.get_catalyst_data(symbol)
            raw["catalyst"] = {
                "events": catalyst.get("events") or catalyst.get("catalysts") or [],
                "summary": catalyst.get("summary") or catalyst.get("catalyst_summary"),
                "timeframe": catalyst.get("timeframe"),
            }
        except Exception as exc:
            logger.warning("[catalyst] catalyst_data failed: %s", exc)
            raw["catalyst_error"] = str(exc)

        # Risk events (to cross-reference)
        try:
            risk = ds.get_risk_events(symbol, days=180)
            raw["risk_events"] = {
                "items": risk.get("items", [])[:5],
                "severity": risk.get("analysis", {}).get("severity_distribution"),
            }
        except Exception as exc:
            logger.warning("[catalyst] risk_events failed: %s", exc)

        # Build summary
        parts = []
        cat = raw.get("catalyst", {})
        if cat.get("summary"):
            parts.append(f"催化总结：{cat['summary']}")
        events = cat.get("events", [])
        if events:
            event_names = [e.get("name") or e.get("title") or str(e) for e in events[:5]]
            parts.append(f"催化事件：{', '.join(event_names)}")
        if cat.get("timeframe"):
            parts.append(f"时间窗口：{cat['timeframe']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return CATALYST_EVENTS
```

- [ ] **Step 3: Verify imports**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.evaluators.growth_drivers import GrowthDriversEvaluator; from src.services.buy_criteria.evaluators.catalyst_events import CatalystEventsEvaluator; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/services/buy_criteria/evaluators/growth_drivers.py src/services/buy_criteria/evaluators/catalyst_events.py
git commit -m "feat(buy-criteria): add evaluators 5-6 (growth drivers + catalyst events)"
```

---

## Task 8: Evaluators ⑦⑧ (Valuation + Fatal Risks)

**Files:**
- Create: `src/services/buy_criteria/evaluators/valuation_level.py`
- Create: `src/services/buy_criteria/evaluators/fatal_risks.py`

- [ ] **Step 1: Create evaluator ⑦ — 估值水位**

```python
# src/services/buy_criteria/evaluators/valuation_level.py
"""Evaluator ⑦: 估值水位 — Is the valuation not透支 (overpriced)?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import VALUATION_LEVEL

logger = logging.getLogger(__name__)


class ValuationLevelEvaluator(BaseCriterionEvaluator):
    criterion_id = "valuation_level"
    criterion_name = "估值水位"
    index = 6

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Valuation ratios (PE, PB, percentiles, PEG)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["valuation"] = {
                "pe_ttm": valuation.get("pe_ttm"),
                "pb": valuation.get("pb"),
                "peg": valuation.get("peg"),
                "pe_percentile": valuation.get("pe_percentile") or valuation.get("pe_history_percentile"),
                "pb_percentile": valuation.get("pb_percentile") or valuation.get("pb_history_percentile"),
                "industry_average": valuation.get("industry_average"),
                "valuation_status": valuation.get("valuation_status"),
            }
        except Exception as exc:
            logger.warning("[valuation] valuation_ratios failed: %s", exc)
            raw["valuation_error"] = str(exc)

        # Price overdraft signal
        try:
            overdraft = ds.get_price_overdraft_signal(symbol)
            raw["overdraft"] = {
                "status": overdraft.get("status"),
                "score": overdraft.get("score"),
                "reasoning": overdraft.get("reasoning"),
            }
        except Exception as exc:
            logger.warning("[valuation] overdraft failed: %s", exc)

        # Build summary
        parts = []
        v = raw.get("valuation", {})
        if v.get("pe_ttm") is not None:
            parts.append(f"PE(TTM): {v['pe_ttm']}")
        if v.get("pe_percentile") is not None:
            parts.append(f"PE历史分位：{v['pe_percentile']}%")
        if v.get("peg") is not None:
            parts.append(f"PEG: {v['peg']}")
        if v.get("industry_average"):
            parts.append(f"行业均值：PE {v['industry_average'].get('pe', '?')}, PB {v['industry_average'].get('pb', '?')}")
        od = raw.get("overdraft", {})
        if od.get("status"):
            parts.append(f"透支信号：{od['status']}（{od.get('reasoning', '')}）")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return VALUATION_LEVEL
```

- [ ] **Step 2: Create evaluator ⑧ — 致命风险**

```python
# src/services/buy_criteria/evaluators/fatal_risks.py
"""Evaluator ⑧: 致命风险 — No unexploded fatal risks?"""
from __future__ import annotations

import logging
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import FATAL_RISKS

logger = logging.getLogger(__name__)


class FatalRisksEvaluator(BaseCriterionEvaluator):
    criterion_id = "fatal_risks"
    criterion_name = "致命风险"
    index = 7

    def collect_data(self, symbol: str, stock_info: dict[str, Any]) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        # Risk events
        try:
            risk = ds.get_risk_events(symbol, days=180)
            raw["risk_events"] = {
                "items": risk.get("items", []),
                "severity": risk.get("analysis", {}).get("severity_distribution"),
                "top_labels": risk.get("analysis", {}).get("top_risk_labels"),
            }
        except Exception as exc:
            logger.warning("[fatal_risks] risk_events failed: %s", exc)
            raw["risk_events_error"] = str(exc)

        # Shareholder structure (pledge, reduction signals)
        try:
            shareholder = ds.get_shareholder_structure(symbol)
            raw["shareholder"] = {
                "pledge_ratio": shareholder.get("pledge_ratio") or shareholder.get("total_pledge_ratio"),
                "top_holder_changes": shareholder.get("top_holder_changes") or shareholder.get("changes"),
                "reduction_signals": shareholder.get("reduction_signals"),
            }
        except Exception as exc:
            logger.warning("[fatal_risks] shareholder failed: %s", exc)

        # Valuation (for financial anomaly signals)
        try:
            valuation = ds.get_valuation_ratios(symbol)
            raw["financial_signals"] = {
                "goodwill": valuation.get("goodwill"),
                "receivables_ratio": valuation.get("receivables_ratio"),
                "cashflow_to_profit": valuation.get("cashflow_to_profit"),
            }
        except Exception as exc:
            logger.warning("[fatal_risks] valuation failed: %s", exc)

        # Build summary
        parts = []
        re_data = raw.get("risk_events", {})
        severity = re_data.get("severity", {})
        if severity:
            parts.append(f"风险事件分布：高危{severity.get('high', 0)}个，中危{severity.get('medium', 0)}个")
        top_labels = re_data.get("top_labels")
        if top_labels:
            parts.append(f"主要风险类型：{', '.join(top_labels[:3])}")
        sh = raw.get("shareholder", {})
        if sh.get("pledge_ratio") is not None:
            parts.append(f"大股东质押比例：{sh['pledge_ratio']}%")
        if sh.get("reduction_signals"):
            parts.append(f"减持信号：{sh['reduction_signals']}")
        fs = raw.get("financial_signals", {})
        if fs.get("goodwill") is not None:
            parts.append(f"商誉：{fs['goodwill']}")

        summary = "；".join(parts) if parts else "数据获取不完整"
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return FATAL_RISKS
```

- [ ] **Step 3: Verify all 8 evaluators import via registry**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.evaluators import EVALUATOR_CLASSES; print(f'{len(EVALUATOR_CLASSES)} evaluators loaded'); [print(f'  {e.index}: {e.criterion_id} — {e.criterion_name}') for e in [cls() for cls in EVALUATOR_CLASSES]]"`
Expected: `8 evaluators loaded` + list of all 8

- [ ] **Step 4: Commit**

```bash
git add src/services/buy_criteria/evaluators/valuation_level.py src/services/buy_criteria/evaluators/fatal_risks.py
git commit -m "feat(buy-criteria): add evaluators 7-8 (valuation level + fatal risks)"
```

---

## Task 9: CriterionOrchestrator + SSE Generator

**Files:**
- Create: `src/services/buy_criteria/orchestrator.py`

- [ ] **Step 1: Create orchestrator**

```python
# src/services/buy_criteria/orchestrator.py
"""Orchestrates sequential execution of 8 criterion evaluators with SSE output."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Optional

from src.services.buy_criteria.base import CriterionResult
from src.services.buy_criteria.evaluators import EVALUATOR_CLASSES

logger = logging.getLogger(__name__)


def _format_sse(event_type: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class CriterionOrchestrator:
    """Runs 8 evaluators sequentially. Stops on first failure. Emits SSE events."""

    def run(self, symbol: str) -> list[CriterionResult]:
        """Run all evaluators. Returns list of results (may be partial if early-terminated)."""
        from api.v1.endpoints.stock_info import get_stock_info

        stock_info = get_stock_info(symbol)
        results: list[CriterionResult] = []

        for evaluator_cls in EVALUATOR_CLASSES:
            evaluator = evaluator_cls()
            result = evaluator.evaluate(symbol, stock_info)
            results.append(result)

            if not result.passed:
                logger.info(
                    "[buy_criteria] %s failed for %s — early termination",
                    evaluator.criterion_id, symbol,
                )
                break

        return results

    def run_with_sse(self, symbol: str, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        """Run evaluators and push SSE events to the queue. Designed for threading worker."""
        from api.v1.endpoints.stock_info import get_stock_info

        SENTINEL_DONE = object()

        try:
            stock_info = get_stock_info(symbol)
            results: list[CriterionResult] = []

            for evaluator_cls in EVALUATOR_CLASSES:
                evaluator = evaluator_cls()

                # Emit criterion_start
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("criterion_start", {
                        "criterion_id": evaluator.criterion_id,
                        "criterion_name": evaluator.criterion_name,
                        "index": evaluator.index,
                    }),
                )

                # Run evaluation
                result = evaluator.evaluate(symbol, stock_info)
                results.append(result)

                # Emit criterion_complete
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("criterion_complete", result.to_dict()),
                )

                # Early termination
                if not result.passed:
                    break

            # Emit analysis_complete
            passed_count = sum(1 for r in results if r.passed)
            failed_count = sum(1 for r in results if not r.passed)
            not_evaluated = 8 - len(results)
            stopped_at = None
            for r in results:
                if not r.passed:
                    stopped_at = r.criterion_id
                    break

            final_decision = "可买入" if passed_count == 8 and failed_count == 0 else "不可买入"
            if failed_count > 0:
                summary = f"共{passed_count}项通过，{failed_count}项未通过（{stopped_at}），不可买入"
            elif not_evaluated > 0:
                summary = f"共{passed_count}项通过，但评估过程异常终止"
            else:
                summary = "8项准则全部通过，可买入"

            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("analysis_complete", {
                    "final_decision": final_decision,
                    "passed_count": passed_count,
                    "failed_count": failed_count,
                    "not_evaluated_count": not_evaluated,
                    "stopped_at": stopped_at,
                    "summary": summary,
                }),
            )
        except Exception as exc:
            logger.error("[buy_criteria] orchestrator error: %s", exc, exc_info=True)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("error", {"criterion_id": "", "message": str(exc)}),
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_DONE, None))

        # Store SENTINEL_DONE reference for the event generator
        self._sentinel = SENTINEL_DONE

    @staticmethod
    def make_sse_endpoint(symbol: str):
        """Create an SSE StreamingResponse for the given symbol.

        Usage in a FastAPI endpoint:
            return CriterionOrchestrator.make_sse_endpoint(symbol)
        """
        from fastapi.responses import StreamingResponse

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL_DONE = object()

        def _worker():
            try:
                stock_info_data = _get_stock_info_safe(symbol)
                results: list[CriterionResult] = []

                for evaluator_cls in EVALUATOR_CLASSES:
                    evaluator = evaluator_cls()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("criterion_start", {
                            "criterion_id": evaluator.criterion_id,
                            "criterion_name": evaluator.criterion_name,
                            "index": evaluator.index,
                        }),
                    )
                    result = evaluator.evaluate(symbol, stock_info_data)
                    results.append(result)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ("criterion_complete", result.to_dict()),
                    )
                    if not result.passed:
                        break

                passed_count = sum(1 for r in results if r.passed)
                failed_count = sum(1 for r in results if not r.passed)
                not_evaluated = 8 - len(results)
                stopped_at = next((r.criterion_id for r in results if not r.passed), None)
                final_decision = "可买入" if passed_count == 8 and failed_count == 0 else "不可买入"
                summary_parts = [f"{passed_count}项通过"]
                if failed_count:
                    summary_parts.append(f"{failed_count}项未通过")
                summary = "，".join(summary_parts) + ("，可买入" if passed_count == 8 else "，不可买入")

                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("analysis_complete", {
                        "final_decision": final_decision,
                        "passed_count": passed_count,
                        "failed_count": failed_count,
                        "not_evaluated_count": not_evaluated,
                        "stopped_at": stopped_at,
                        "summary": summary,
                    }),
                )
            except Exception as exc:
                logger.error("[buy_criteria] orchestrator error: %s", exc, exc_info=True)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("error", {"criterion_id": "", "message": str(exc)}),
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, (SENTINEL_DONE, None))

        threading.Thread(target=_worker, daemon=True).start()

        async def event_generator():
            import asyncio as _aio
            while True:
                try:
                    item = await _aio.wait_for(queue.get(), timeout=15)
                except _aio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event_type, data = item
                if event_type is SENTINEL_DONE:
                    break
                yield _format_sse(event_type, data)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


def _get_stock_info_safe(symbol: str) -> dict[str, Any]:
    """Get stock info with error handling."""
    try:
        from api.v1.endpoints.stock_info import get_stock_info
        return get_stock_info(symbol)
    except Exception as exc:
        logger.error("[buy_criteria] failed to get stock_info for %s: %s", symbol, exc)
        return {"symbol": symbol, "name": symbol, "industry": ""}
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from src.services.buy_criteria.orchestrator import CriterionOrchestrator; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/services/buy_criteria/orchestrator.py
git commit -m "feat(buy-criteria): add CriterionOrchestrator with SSE streaming support"
```

---

## Task 10: API Endpoint

**Files:**
- Modify: `api/v1/endpoints/buy_decision.py`

- [ ] **Step 1: Add the criteria analysis endpoint to the existing router**

Add this to the top of `buy_decision.py` (imports section):

```python
from api.v1.schemas.buy_criteria import CriteriaAnalyzeRequest
```

Add this new endpoint function at the bottom of the file:

```python
# ── Criteria Analysis (new) ─────────────────────────────────────────────


@router.get(
    "/criteria/analyze",
    summary="买入准则分析（SSE流）",
    description="顺序评估8项买入准则，通过SSE实时推送结果。任意一项不通过即终止。",
)
async def analyze_buy_criteria(symbol: str = Query(..., description="股票代码")):
    """Stream criterion evaluation results via SSE."""
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=400, detail="symbol is required")

    from src.services.buy_criteria.orchestrator import CriterionOrchestrator
    return CriterionOrchestrator.make_sse_endpoint(symbol.strip())
```

- [ ] **Step 2: Verify the server starts**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -c "from api.v1.endpoints.buy_decision import router; print(f'{len(router.routes)} routes loaded')"`
Expected: A number ≥ 6 (existing 5 + new 1)

- [ ] **Step 3: Commit**

```bash
git add api/v1/endpoints/buy_decision.py
git commit -m "feat(buy-criteria): add GET /criteria/analyze SSE endpoint"
```

---

## Task 11: Backend Tests

**Files:**
- Create: `tests/test_buy_criteria.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_buy_criteria.py
"""Tests for buy criteria analysis."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.buy_criteria.base import (
    BaseCriterionEvaluator,
    CriterionEvidence,
    CriterionResult,
    _parse_verdict_json,
)
from src.services.buy_criteria.evaluators import EVALUATOR_CLASSES
from src.services.buy_criteria.orchestrator import CriterionOrchestrator, _format_sse


# ── Unit Tests: _parse_verdict_json ─────────────────────────────────────


class TestParseVerdictJson:
    def test_plain_json(self):
        raw = '{"passed": true, "verdict": "行业景气上行"}'
        result = _parse_verdict_json(raw)
        assert result is not None
        assert result["passed"] is True
        assert "verdict" in result

    def test_markdown_fenced(self):
        raw = '```json\n{"passed": false, "verdict": "不通过"}\n```'
        result = _parse_verdict_json(raw)
        assert result is not None
        assert result["passed"] is False

    def test_embedded_in_text(self):
        raw = '根据分析，结果如下：\n{"passed": true, "verdict": "通过"}\n以上是结果。'
        result = _parse_verdict_json(raw)
        assert result is not None
        assert result["passed"] is True

    def test_invalid_returns_none(self):
        assert _parse_verdict_json("not json at all") is None
        assert _parse_verdict_json("") is None


# ── Unit Tests: CriterionResult ─────────────────────────────────────────


class TestCriterionResult:
    def test_to_dict(self):
        r = CriterionResult(
            criterion_id="test",
            criterion_name="测试",
            index=0,
            passed=True,
            verdict="通过",
            evidence=CriterionEvidence(raw_data={"key": "val"}, data_summary="摘要"),
        )
        d = r.to_dict()
        assert d["criterion_id"] == "test"
        assert d["passed"] is True
        assert d["evidence"]["raw_data"] == {"key": "val"}
        assert d["analyzed_at"]  # auto-populated


# ── Unit Tests: Evaluator Registry ──────────────────────────────────────


class TestEvaluatorRegistry:
    def test_eight_evaluators(self):
        assert len(EVALUATOR_CLASSES) == 8

    def test_indices_sequential(self):
        instances = [cls() for cls in EVALUATOR_CLASSES]
        indices = [e.index for e in instances]
        assert indices == list(range(8))

    def test_all_have_rubrics(self):
        for cls in EVALUATOR_CLASSES:
            e = cls()
            rubric = e.get_rubric()
            assert isinstance(rubric, str)
            assert len(rubric) > 50  # rubrics should be substantial


# ── Unit Tests: BaseCriterionEvaluator with mocked LLM ──────────────────


class TestBaseEvaluatorWithMockedLLM:
    def test_evaluate_pass(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value={"passed": True, "verdict": "核心主线，资金持续流入"}):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is True
        assert "核心主线" in result.verdict
        assert result.criterion_id == evaluator.criterion_id

    def test_evaluate_fail(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value={"passed": False, "verdict": "非主线"}):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is False

    def test_evaluate_llm_failure_returns_not_passed(self):
        evaluator = EVALUATOR_CLASSES[0]()
        mock_evidence = CriterionEvidence(raw_data={}, data_summary="test data")

        with patch.object(evaluator, "collect_data", return_value=mock_evidence):
            with patch.object(evaluator, "_call_llm", return_value=None):
                result = evaluator.evaluate("300308", {"symbol": "300308", "name": "中际旭创", "industry": "通信设备"})

        assert result.passed is False
        assert "评估失败" in result.verdict


# ── Unit Tests: Orchestrator ─────────────────────────────────────────────


class TestOrchestrator:
    def test_early_termination(self):
        """If evaluator 0 fails, only 1 result should be returned."""
        orchestrator = CriterionOrchestrator()
        mock_result_fail = CriterionResult(
            criterion_id="mainline_position", criterion_name="市场主线属性",
            index=0, passed=False, verdict="非主线",
        )

        mock_stock_info = {"symbol": "000001", "name": "测试", "industry": "测试"}

        with patch("src.services.buy_criteria.orchestrator._get_stock_info_safe", return_value=mock_stock_info):
            with patch.object(EVALUATOR_CLASSES[0], "evaluate", return_value=mock_result_fail):
                results = orchestrator.run("000001")

        assert len(results) == 1
        assert results[0].passed is False

    def test_all_pass(self):
        """If all 8 evaluators pass, 8 results should be returned."""
        orchestrator = CriterionOrchestrator()
        mock_stock_info = {"symbol": "000001", "name": "测试", "industry": "测试"}

        def make_pass_result(idx):
            cls = EVALUATOR_CLASSES[idx]
            e = cls()
            return CriterionResult(
                criterion_id=e.criterion_id, criterion_name=e.criterion_name,
                index=idx, passed=True, verdict="通过",
            )

        with patch("src.services.buy_criteria.orchestrator._get_stock_info_safe", return_value=mock_stock_info):
            for i, cls in enumerate(EVALUATOR_CLASSES):
                patch.object(cls, "evaluate", return_value=make_pass_result(i)).start()

            results = orchestrator.run("000001")

        assert len(results) == 8
        assert all(r.passed for r in results)


# ── Unit Tests: SSE Format ───────────────────────────────────────────────


class TestSSEFormat:
    def test_format_sse(self):
        result = _format_sse("test_event", {"key": "值"})
        assert "event: test_event" in result
        assert '"key"' in result
        assert "值" in result
        assert result.endswith("\n\n")
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -m pytest tests/test_buy_criteria.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_buy_criteria.py
git commit -m "test(buy-criteria): add backend tests for evaluators, orchestrator, SSE"
```

---

## Task 12: Frontend API Types + Client

**Files:**
- Create: `apps/dsa-web/src/api/buyCriteria.ts`

- [ ] **Step 1: Create the API client and types**

```typescript
// apps/dsa-web/src/api/buyCriteria.ts
import apiClient from './index';

// ── Types ────────────────────────────────────────────────────────────────

export type CriterionId =
  | 'mainline_position'
  | 'prosperity_cycle'
  | 'growth_space'
  | 'competition_landscape'
  | 'growth_drivers'
  | 'catalyst_events'
  | 'valuation_level'
  | 'fatal_risks';

export type CriterionStatus = 'idle' | 'running' | 'pass' | 'fail' | 'not_evaluated';

export interface CriterionEvidence {
  raw_data: Record<string, unknown>;
  data_summary: string;
}

export interface CriterionResult {
  criterion_id: CriterionId;
  criterion_name: string;
  index: number;
  passed: boolean;
  verdict: string;
  evidence: CriterionEvidence;
  analyzed_at: string;
}

export interface CriterionStartEvent {
  criterion_id: CriterionId;
  criterion_name: string;
  index: number;
}

export interface AnalysisCompleteEvent {
  final_decision: '可买入' | '不可买入';
  passed_count: number;
  failed_count: number;
  not_evaluated_count: number;
  stopped_at: CriterionId | null;
  summary: string;
}

export interface AnalysisErrorEvent {
  criterion_id: string;
  message: string;
}

// SSE event types
export type CriteriaSSEEventType =
  | 'criterion_start'
  | 'criterion_complete'
  | 'analysis_complete'
  | 'error';

// ── Criterion metadata (ordered) ────────────────────────────────────────

export const CRITERIA_ORDER: { id: CriterionId; name: string }[] = [
  { id: 'mainline_position', name: '市场主线属性' },
  { id: 'prosperity_cycle', name: '景气上行周期' },
  { id: 'growth_space', name: '未来3年空间' },
  { id: 'competition_landscape', name: '竞争格局' },
  { id: 'growth_drivers', name: '驱动因素' },
  { id: 'catalyst_events', name: '催化事件' },
  { id: 'valuation_level', name: '估值水位' },
  { id: 'fatal_risks', name: '致命风险' },
];

// ── API ─────────────────────────────────────────────────────────────────

export const buyCriteriaApi = {
  /** Get the SSE URL for criteria analysis. */
  getCriteriaStreamUrl(symbol: string): string {
    const base = apiClient.defaults.baseURL || '';
    return `${base}/api/v1/stocks/buy-decision/criteria/analyze?symbol=${encodeURIComponent(symbol)}`;
  },
};
```

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npx tsc --noEmit src/api/buyCriteria.ts 2>&1 | head -20`
Expected: No errors (or only pre-existing project-wide errors)

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/api/buyCriteria.ts
git commit -m "feat(buy-criteria): add frontend API types and client"
```

---

## Task 13: useBuyCriteria Hook

**Files:**
- Create: `apps/dsa-web/src/hooks/useBuyCriteria.ts`

- [ ] **Step 1: Create the hook**

```typescript
// apps/dsa-web/src/hooks/useBuyCriteria.ts
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AnalysisCompleteEvent,
  CriterionId,
  CriterionResult,
  CriterionStatus,
  CRITERIA_ORDER,
  buyCriteriaApi,
} from '../api/buyCriteria';

// ── Types ────────────────────────────────────────────────────────────────

interface CriterionState {
  status: CriterionStatus;
  result: CriterionResult | null;
}

interface AnalysisState {
  criteria: Record<CriterionId, CriterionState>;
  finalDecision: '可买入' | '不可买入' | null;
  analysisSummary: string;
  isRunning: boolean;
  error: string | null;
}

interface UseBuyCriteriaReturn {
  state: AnalysisState;
  startAnalysis: (symbol: string) => void;
  stopAnalysis: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────

function createInitialState(): AnalysisState {
  const criteria = {} as Record<CriterionId, CriterionState>;
  for (const c of CRITERIA_ORDER) {
    criteria[c.id] = { status: 'idle', result: null };
  }
  return { criteria, finalDecision: null, analysisSummary: '', isRunning: false, error: null };
}

// ── Hook ─────────────────────────────────────────────────────────────────

export function useBuyCriteria(): UseBuyCriteriaReturn {
  const [state, setState] = useState<AnalysisState>(createInitialState);
  const eventSourceRef = useRef<EventSource | null>(null);

  const stopAnalysis = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setState(prev => ({ ...prev, isRunning: false }));
  }, []);

  const startAnalysis = useCallback((symbol: string) => {
    // Stop any existing analysis
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    // Reset state
    setState({ ...createInitialState(), isRunning: true });

    const url = buyCriteriaApi.getCriteriaStreamUrl(symbol);
    const es = new EventSource(url, { withCredentials: true });
    eventSourceRef.current = es;

    es.addEventListener('criterion_start', (event: MessageEvent) => {
      const data = JSON.parse(event.data) as { criterion_id: CriterionId };
      setState(prev => ({
        ...prev,
        criteria: {
          ...prev.criteria,
          [data.criterion_id]: { status: 'running', result: null },
        },
      }));
    });

    es.addEventListener('criterion_complete', (event: MessageEvent) => {
      const result = JSON.parse(event.data) as CriterionResult;
      setState(prev => {
        const newCriteria = {
          ...prev.criteria,
          [result.criterion_id]: {
            status: result.passed ? 'pass' : 'fail',
            result,
          },
        };

        // If this criterion failed, mark remaining as not_evaluated
        if (!result.passed) {
          for (const c of CRITERIA_ORDER) {
            if (newCriteria[c.id].status === 'idle') {
              newCriteria[c.id] = { status: 'not_evaluated', result: null };
            }
          }
        }

        return { ...prev, criteria: newCriteria };
      });
    });

    es.addEventListener('analysis_complete', (event: MessageEvent) => {
      const data = JSON.parse(event.data) as AnalysisCompleteEvent;
      setState(prev => ({
        ...prev,
        finalDecision: data.final_decision,
        analysisSummary: data.summary,
        isRunning: false,
      }));
      es.close();
      eventSourceRef.current = null;
    });

    es.addEventListener('error', (event: MessageEvent) => {
      // SSE error event with data = backend error
      if (event.data) {
        try {
          const data = JSON.parse(event.data) as { message: string };
          setState(prev => ({
            ...prev,
            error: data.message || '分析出错',
            isRunning: false,
          }));
        } catch {
          // Network-level EventSource error
          setState(prev => ({
            ...prev,
            error: '连接中断',
            isRunning: false,
          }));
        }
      }
      es.close();
      eventSourceRef.current = null;
    });

    es.onerror = () => {
      setState(prev => {
        if (prev.isRunning) {
          return { ...prev, error: '连接中断', isRunning: false };
        }
        return prev;
      });
      es.close();
      eventSourceRef.current = null;
    };
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return { state, startAnalysis, stopAnalysis };
}
```

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npx tsc --noEmit src/hooks/useBuyCriteria.ts 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/hooks/useBuyCriteria.ts
git commit -m "feat(buy-criteria): add useBuyCriteria hook with SSE subscription"
```

---

## Task 14: CriterionCard Component

**Files:**
- Create: `apps/dsa-web/src/components/buyCriteria/CriterionCard.tsx`

- [ ] **Step 1: Create the component**

```tsx
// apps/dsa-web/src/components/buyCriteria/CriterionCard.tsx
import { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, LoaderCircle, Circle } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { CriterionResult, CriterionStatus } from '../../api/buyCriteria';

interface CriterionCardProps {
  criterionId: string;
  criterionName: string;
  index: number;
  status: CriterionStatus;
  result: CriterionResult | null;
}

const STATUS_CONFIG: Record<CriterionStatus, { border: string; bg: string; iconColor: string }> = {
  idle: { border: 'border-slate-200', bg: 'bg-slate-50', iconColor: 'text-slate-300' },
  running: { border: 'border-cyan-300', bg: 'bg-cyan-50/50', iconColor: 'text-cyan-500' },
  pass: { border: 'border-emerald-300', bg: 'bg-white', iconColor: 'text-emerald-500' },
  fail: { border: 'border-rose-300', bg: 'bg-white', iconColor: 'text-rose-500' },
  not_evaluated: { border: 'border-slate-200', bg: 'bg-slate-50/50', iconColor: 'text-slate-300' },
};

const NUM_LABELS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧'];

export function CriterionCard({ criterionId, criterionName, index, status, result }: CriterionCardProps) {
  const [dataExpanded, setDataExpanded] = useState(false);
  const config = STATUS_CONFIG[status];

  return (
    <div
      className={cn(
        'rounded-2xl border-2 transition-all duration-300',
        config.border,
        config.bg,
        status === 'not_evaluated' && 'opacity-50',
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3 p-4">
        {/* Status icon */}
        <div className="flex-shrink-0 mt-0.5">
          {status === 'idle' && <Circle className={cn('h-6 w-6', config.iconColor)} />}
          {status === 'running' && <LoaderCircle className={cn('h-6 w-6 animate-spin', config.iconColor)} />}
          {status === 'pass' && <CheckCircle2 className={cn('h-6 w-6', config.iconColor)} />}
          {status === 'fail' && <XCircle className={cn('h-6 w-6', config.iconColor)} />}
          {status === 'not_evaluated' && <Circle className={cn('h-6 w-6', config.iconColor)} />}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="text-[15px] font-semibold text-slate-800">
              {NUM_LABELS[index]} {criterionName}
            </span>
            {status === 'pass' && (
              <span className="text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full">
                通过
              </span>
            )}
            {status === 'fail' && (
              <span className="text-xs font-medium text-rose-700 bg-rose-50 px-2.5 py-0.5 rounded-full">
                未通过
              </span>
            )}
            {status === 'running' && (
              <span className="text-xs font-medium text-cyan-700 bg-cyan-50 px-2.5 py-0.5 rounded-full">
                分析中...
              </span>
            )}
            {status === 'not_evaluated' && (
              <span className="text-xs text-slate-400">未评估</span>
            )}
          </div>

          {/* Verdict or status text */}
          {status === 'pass' && result && (
            <p className="text-[13px] text-slate-600 leading-relaxed">{result.verdict}</p>
          )}
          {status === 'fail' && result && (
            <p className="text-[13px] text-slate-600 leading-relaxed">{result.verdict}</p>
          )}
          {status === 'running' && (
            <p className="text-[13px] text-slate-400">正在采集数据并分析...</p>
          )}
          {status === 'idle' && (
            <p className="text-[13px] text-slate-400">等待分析</p>
          )}
          {status === 'not_evaluated' && (
            <p className="text-[13px] text-slate-400">前置准则未通过，未评估</p>
          )}

          {/* Collapsible data details (only when result exists) */}
          {result && (status === 'pass' || status === 'fail') && (
            <div className="mt-2 border-t border-slate-100 pt-2">
              <button
                onClick={() => setDataExpanded(!dataExpanded)}
                className="flex items-center gap-1 text-xs text-cyan-600 hover:text-cyan-700 transition-colors"
              >
                {dataExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                查看底层数据明细
              </button>
              {dataExpanded && (
                <div className="mt-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 font-mono leading-relaxed max-h-64 overflow-y-auto">
                  <pre className="whitespace-pre-wrap break-all">
                    {JSON.stringify(result.evidence.raw_data, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npx tsc --noEmit src/components/buyCriteria/CriterionCard.tsx 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add apps/dsa-web/src/components/buyCriteria/CriterionCard.tsx
git commit -m "feat(buy-criteria): add CriterionCard component with 5 states and collapsible data"
```

---

## Task 15: SummaryBar + BuyCriteriaPanel + Integration

**Files:**
- Create: `apps/dsa-web/src/components/buyCriteria/SummaryBar.tsx`
- Create: `apps/dsa-web/src/components/buyCriteria/BuyCriteriaPanel.tsx`
- Modify: `apps/dsa-web/src/pages/StockAnalysisPage.tsx`

- [ ] **Step 1: Create SummaryBar**

```tsx
// apps/dsa-web/src/components/buyCriteria/SummaryBar.tsx
import { CheckCircle2, XCircle, Circle, RefreshCw, Play } from 'lucide-react';
import { Button } from '../common/Button';
import { cn } from '../../utils/cn';
import { CRITERIA_ORDER } from '../../api/buyCriteria';
import type { CriterionStatus } from '../../api/buyCriteria';

interface SummaryBarProps {
  symbol: string;
  stockName: string;
  criteriaStatuses: Record<string, CriterionStatus>;
  finalDecision: '可买入' | '不可买入' | null;
  summary: string;
  isRunning: boolean;
  onStart: () => void;
  onRestart: () => void;
}

export function SummaryBar({
  symbol,
  stockName,
  criteriaStatuses,
  finalDecision,
  summary,
  isRunning,
  onStart,
  onRestart,
}: SummaryBarProps) {
  const passed = Object.values(criteriaStatuses).filter(s => s === 'pass').length;
  const failed = Object.values(criteriaStatuses).filter(s => s === 'fail').length;
  const notEvaluated = Object.values(criteriaStatuses).filter(s => s === 'not_evaluated').length;
  const hasResults = passed > 0 || failed > 0;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        {/* Left: stock info + counts */}
        <div className="flex items-center gap-4">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-400">
              买入判定
            </div>
            <div className="text-lg font-bold text-slate-800 mt-0.5">
              {stockName || symbol}
            </div>
          </div>

          {hasResults && (
            <>
              <div className="h-9 w-px bg-slate-200" />
              <div className="flex gap-1.5">
                <span className={cn(
                  'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium',
                  passed > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-50 text-slate-400',
                )}>
                  <CheckCircle2 className="h-3.5 w-3.5" /> {passed}
                </span>
                <span className={cn(
                  'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium',
                  failed > 0 ? 'bg-rose-50 text-rose-700' : 'bg-slate-50 text-slate-400',
                )}>
                  <XCircle className="h-3.5 w-3.5" /> {failed}
                </span>
                {notEvaluated > 0 && (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-50 text-slate-400">
                    <Circle className="h-3.5 w-3.5" /> {notEvaluated}
                  </span>
                )}
              </div>
            </>
          )}
        </div>

        {/* Right: decision + actions */}
        <div className="flex items-center gap-3">
          {finalDecision && (
            <span className={cn(
              'text-sm font-semibold',
              finalDecision === '可买入' ? 'text-emerald-600' : 'text-rose-600',
            )}>
              {finalDecision === '可买入' ? '✅' : '❌'} {finalDecision}
            </span>
          )}
          {summary && !isRunning && (
            <span className="text-xs text-slate-500">{summary}</span>
          )}
          {!hasResults && !isRunning && (
            <Button onClick={onStart} size="sm">
              <Play className="h-4 w-4 mr-1" />
              开始分析
            </Button>
          )}
          {hasResults && !isRunning && (
            <Button onClick={onRestart} variant="outline" size="sm">
              <RefreshCw className="h-4 w-4 mr-1" />
              重新分析
            </Button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="flex gap-1 mt-3">
        {CRITERIA_ORDER.map((c) => {
          const status = criteriaStatuses[c.id];
          return (
            <div
              key={c.id}
              className={cn(
                'h-1 flex-1 rounded-full transition-colors duration-300',
                status === 'pass' && 'bg-emerald-400',
                status === 'fail' && 'bg-rose-400',
                status === 'running' && 'bg-cyan-400 animate-pulse',
                status === 'idle' && 'bg-slate-200',
                status === 'not_evaluated' && 'bg-slate-100',
              )}
            />
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create BuyCriteriaPanel**

```tsx
// apps/dsa-web/src/components/buyCriteria/BuyCriteriaPanel.tsx
import { useEffect } from 'react';
import { useBuyCriteria } from '../../hooks/useBuyCriteria';
import { CRITERIA_ORDER } from '../../api/buyCriteria';
import { SummaryBar } from './SummaryBar';
import { CriterionCard } from './CriterionCard';

interface BuyCriteriaPanelProps {
  symbol: string;
}

export function BuyCriteriaPanel({ symbol }: BuyCriteriaPanelProps) {
  const { state, startAnalysis, stopAnalysis } = useBuyCriteria();

  // Reset when symbol changes
  useEffect(() => {
    stopAnalysis();
  }, [symbol, stopAnalysis]);

  if (!symbol) {
    return (
      <div className="rounded-2xl border-2 border-dashed border-slate-200 p-8 text-center text-slate-400">
        请先选择一只股票
      </div>
    );
  }

  const criteriaStatuses = Object.fromEntries(
    Object.entries(state.criteria).map(([id, cs]) => [id, cs.status]),
  );

  return (
    <div className="space-y-4">
      <SummaryBar
        symbol={symbol}
        stockName={symbol}  // Will show symbol; real name can be fetched from stock_info
        criteriaStatuses={criteriaStatuses}
        finalDecision={state.finalDecision}
        summary={state.analysisSummary}
        isRunning={state.isRunning}
        onStart={() => startAnalysis(symbol)}
        onRestart={() => startAnalysis(symbol)}
      />

      {state.error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {state.error}
        </div>
      )}

      <div className="space-y-3">
        {CRITERIA_ORDER.map((c) => {
          const cs = state.criteria[c.id];
          return (
            <CriterionCard
              key={c.id}
              criterionId={c.id}
              criterionName={c.name}
              index={c.index}
              status={cs.status}
              result={cs.result}
            />
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Integrate into StockAnalysisPage**

In `apps/dsa-web/src/pages/StockAnalysisPage.tsx`:

Replace the import at the top:
```typescript
// REMOVE:
import { BuyDecisionWorkbench } from '../components/buyDecision/BuyDecisionWorkbench';
// ADD:
import { BuyCriteriaPanel } from '../components/buyCriteria/BuyCriteriaPanel';
```

Replace the tab rendering (around line 1599-1600):
```tsx
// REMOVE:
) : mode === 'industry-cycle' ? (
  <BuyDecisionWorkbench symbol={selectedSymbol} />
// REPLACE WITH:
) : mode === 'industry-cycle' ? (
  <BuyCriteriaPanel symbol={selectedSymbol} />
```

- [ ] **Step 4: Verify the build**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npm run build 2>&1 | tail -20`
Expected: Build succeeds, no errors

- [ ] **Step 5: Commit**

```bash
git add apps/dsa-web/src/components/buyCriteria/SummaryBar.tsx apps/dsa-web/src/components/buyCriteria/BuyCriteriaPanel.tsx apps/dsa-web/src/pages/StockAnalysisPage.tsx
git commit -m "feat(buy-criteria): add SummaryBar, BuyCriteriaPanel, integrate into StockAnalysisPage"
```

---

## Task 16: Delete Old Code + Final Verification

**Files:**
- Delete: `apps/dsa-web/src/components/buyDecision/BuyDecisionWorkbench.tsx`
- Delete: `apps/dsa-web/src/components/buyDecision/IndustryBetaChat.tsx`
- Delete: `apps/dsa-web/src/hooks/useBuyDecisionWorkbench.ts`
- Delete: `apps/dsa-web/src/api/buyDecisionWorkbench.ts`
- Delete: `src/services/buy_decision_workbench_service.py`
- Delete: `tests/test_buy_decision_api.py`
- Modify: `api/v1/endpoints/buy_decision.py` (remove old routes)

- [ ] **Step 1: Remove old frontend files**

```bash
cd /Users/xiejiawei/Documents/learn/daily_stock_analysis
rm -f apps/dsa-web/src/components/buyDecision/BuyDecisionWorkbench.tsx
rm -f apps/dsa-web/src/components/buyDecision/IndustryBetaChat.tsx
rmdir apps/dsa-web/src/components/buyDecision 2>/dev/null || true
rm -f apps/dsa-web/src/hooks/useBuyDecisionWorkbench.ts
rm -f apps/dsa-web/src/api/buyDecisionWorkbench.ts
```

- [ ] **Step 2: Remove old backend service**

```bash
rm -f src/services/buy_decision_workbench_service.py
rm -f tests/test_buy_decision_api.py
```

- [ ] **Step 3: Clean up old routes from buy_decision.py**

Remove the old workbench routes and imports from `api/v1/endpoints/buy_decision.py`. Keep only:
- The import of `CriteriaAnalyzeRequest`
- The new `/criteria/analyze` endpoint
- Remove: `_service()`, all `workbench` routes, `BuyDecisionWorkbenchService` import

The file should be slim — just the new criteria endpoint and the router setup.

- [ ] **Step 4: Verify build passes**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npm run build 2>&1 | tail -20`
Expected: Build succeeds

- [ ] **Step 5: Run backend tests**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis && python3 -m pytest tests/test_buy_criteria.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run constraint check**

Run: `cd /Users/xiejiawei/Documents/learn/daily_stock_analysis/apps/dsa-web && npm run check:constraints 2>&1 | tail -10`
Expected: Within budgets

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "refactor(buy-criteria): remove old BuyDecisionWorkbench code, finalize criteria analysis"
```

---

## Summary

| Phase | Tasks | Files |
|---|---|---|
| Backend foundation | 1-4 | schemas, base, data_service, rubrics |
| Evaluators | 5-8 | 8 evaluator files |
| Orchestrator + API | 9-10 | orchestrator, endpoint |
| Backend tests | 11 | test file |
| Frontend API + hook | 12-13 | buyCriteria.ts, useBuyCriteria.ts |
| Frontend components | 14-15 | CriterionCard, SummaryBar, BuyCriteriaPanel, StockAnalysisPage |
| Cleanup | 16 | Delete old code, final verification |
