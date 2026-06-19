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
