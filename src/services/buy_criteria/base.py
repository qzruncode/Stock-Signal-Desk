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
    status: str = ""
    confidence: str = ""
    evidence: CriterionEvidence = field(default_factory=CriterionEvidence)
    details: dict[str, Any] = field(default_factory=dict)
    prompt_text: str = ""
    analyzed_at: str = ""

    def __post_init__(self):
        if not self.status:
            self.status = "pass" if self.passed else "fail"
        if not self.analyzed_at:
            self.analyzed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "criterion_name": self.criterion_name,
            "index": self.index,
            "passed": self.passed,
            "status": self.status,
            "confidence": self.confidence,
            "verdict": self.verdict,
            "evidence": {
                "raw_data": self.evidence.raw_data,
                "data_summary": self.evidence.data_summary,
            },
            "details": self.details,
            "prompt_text": self.prompt_text,
            "analyzed_at": self.analyzed_at,
        }


class BaseCriterionEvaluator(ABC):
    """Reusable evidence collector and optional single-dimension evaluator."""

    criterion_id: str = ""
    criterion_name: str = ""
    index: int = -1
    max_output_tokens: int = 2048
    max_llm_attempts: int = 2

    @abstractmethod
    def collect_data(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionEvidence:
        """Collect data needed for this criterion. Must not call LLM.

        If ``pre_fetched_data`` is provided, the evaluator should prefer it
        over re-fetching from external sources.
        """
        ...

    @abstractmethod
    def get_rubric(self) -> str:
        """Return the judgment rubric text for the LLM prompt."""
        ...

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        """Return a fail-closed reason for a critical evidence outage."""
        del evidence
        return None

    def build_user_prompt(self, stock_info: dict[str, Any], evidence: CriterionEvidence) -> str:
        """Build the user prompt. Rubric is self-contained (role + criteria + JSON format).

        data_summary may contain ## 股票信息 which the rubric references — no duplication.
        """
        return f"{self.get_rubric()}\n\n{evidence.data_summary}\n\n"

    def evaluate(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionResult:
        """Full evaluation: collect_data → LLM → result. Handles retry."""
        evidence = self.collect_data(symbol, stock_info, pre_fetched_data)
        user_prompt = self.build_user_prompt(stock_info, evidence)
        evidence_failure = self.evidence_failure_reason(evidence)
        if evidence_failure:
            return CriterionResult(
                criterion_id=self.criterion_id,
                criterion_name=self.criterion_name,
                index=self.index,
                passed=False,
                status="insufficient",
                verdict=evidence_failure,
                evidence=evidence,
                prompt_text=user_prompt,
            )

        result, error_msg = self._call_llm(user_prompt, attempt=0)
        if result is None and self.max_llm_attempts > 1:
            # Retry once
            result, retry_error = self._call_llm(user_prompt, attempt=1)
            if retry_error:
                error_msg = retry_error

        if result is None:
            return CriterionResult(
                criterion_id=self.criterion_id,
                criterion_name=self.criterion_name,
                index=self.index,
                passed=False,
                status="insufficient",
                verdict=f"{self.criterion_name}评估失败：{error_msg or 'LLM 未返回有效判断'}",
                evidence=evidence,
                prompt_text=user_prompt,
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
            status="pass" if bool(passed) else "fail",
            confidence=str(result.get("confidence") or ""),
            verdict=verdict,
            evidence=evidence,
            details=result.get("details") if isinstance(result.get("details"), dict) else {},
            prompt_text=user_prompt,
        )

    def _call_llm(self, user_prompt: str, *, attempt: int) -> tuple[Optional[dict[str, Any]], str]:
        """Call LLM and parse JSON response. Returns (result, error_message).
        On success, error_message is empty. On failure, result is None."""
        from src.ai_caller import call_ai_structured
        from src.analyzer import get_analyzer
        from src.storage import persist_llm_usage

        system_prompt = "你是一个专业的A股行业分析师。请严格按照判定标准进行分析，并以JSON格式返回结果。"

        # Custom validator that handles markdown code fences — the default
        # json.loads validator in call_ai_structured rejects fenced JSON.
        def _fence_aware_validator(text: str) -> None:
            payload = _parse_verdict_json(text)
            if payload is None:
                raise ValueError("response does not contain an extractable JSON object")
            passed = payload.get("passed")
            if not isinstance(passed, bool) and str(passed).strip().lower() not in {
                "true", "false", "yes", "no", "是", "否",
            }:
                raise ValueError("response passed must be an explicit boolean")
            if not str(payload.get("verdict") or "").strip():
                raise ValueError("response verdict must explain the Boolean decision")
            if "details" in payload and not isinstance(payload.get("details"), dict):
                raise ValueError("response details must be an object")

        try:
            analyzer = get_analyzer()
            response_text, model_used, usage = call_ai_structured(
                analyzer,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                call_type=f"buy_criteria_{self.criterion_id}",
                temperature=0.2,
                max_tokens=self.max_output_tokens,
                response_validator=_fence_aware_validator,
            )
            persist_llm_usage(usage, model_used, f"buy_criteria_{self.criterion_id}")
            parsed = _parse_verdict_json(response_text)
            if parsed is None:
                return None, f"LLM 返回内容无法解析为 JSON（模型: {model_used}）"
            return parsed, ""
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            # Truncate very long errors
            if len(err_msg) > 200:
                err_msg = err_msg[:200] + "…"
            logger.warning(
                "[buy_criteria] LLM call failed for %s (attempt %d): %s",
                self.criterion_id, attempt, err_msg,
            )
            return None, err_msg


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
