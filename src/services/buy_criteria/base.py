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
        symbol = stock_info.get("symbol", "")
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
