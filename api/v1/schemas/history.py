# -*- coding: utf-8 -*-
"""
===================================
历史记录相关模型
===================================

职责：
1. 定义持久化分析报告的结构化投影
2. 为 Agent 的历史报告读取工具提供统一的数据模型
"""

from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field


class ReportMeta(BaseModel):
    """报告元信息"""

    model_config = ConfigDict(protected_namespaces=("model_validate", "model_dump"))

    id: Optional[int] = Field(None, description="分析历史记录主键 ID（仅历史报告有此字段）")
    query_id: str = Field(..., description="分析记录关联 query_id（批量分析时重复）")
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    report_type: Optional[str] = Field(None, description="报告类型")
    report_language: Optional[str] = Field(None, description="报告输出语言（zh/en）")
    created_at: Optional[str] = Field(None, description="创建时间")
    current_price: Optional[float] = Field(None, description="分析时股价")
    change_pct: Optional[float] = Field(None, description="分析时涨跌幅(%)")
    model_used: Optional[str] = Field(None, description="分析使用的 LLM 模型")


class ReportSummary(BaseModel):
    """报告概览区"""

    analysis_summary: Optional[str] = Field(None, description="关键结论")
    operation_advice: Optional[str] = Field(None, description="操作建议")
    trend_prediction: Optional[str] = Field(None, description="趋势预测")
    sentiment_score: Optional[int] = Field(
        None,
        description="情绪评分（历史数据可能超出 0-100 范围，读取时不做约束）",
    )
    sentiment_label: Optional[str] = Field(None, description="情绪标签")


class ReportStrategy(BaseModel):
    """策略点位区"""

    ideal_buy: Optional[str] = Field(None, description="理想买入价")
    secondary_buy: Optional[str] = Field(None, description="第二买入价")
    stop_loss: Optional[str] = Field(None, description="止损价")
    take_profit: Optional[str] = Field(None, description="止盈价")


class ReportDetails(BaseModel):
    """报告详情区"""

    news_content: Optional[str] = Field(None, description="新闻摘要")
    raw_result: Optional[Any] = Field(None, description="原始分析结果（JSON）")
    context_snapshot: Optional[Any] = Field(None, description="分析时上下文快照（JSON）")
    financial_report: Optional[Any] = Field(None, description="结构化财报摘要（来自 fundamental_context）")
    dividend_metrics: Optional[Any] = Field(None, description="结构化分红指标（含 TTM 口径）")
    belong_boards: Optional[Any] = Field(None, description="关联板块列表")
    sector_rankings: Optional[Any] = Field(None, description="板块涨跌榜（结构 {top, bottom}）")


class AnalysisReport(BaseModel):
    """完整分析报告"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "meta": {
                    "query_id": "abc123",
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "report_type": "detailed",
                    "report_language": "zh",
                    "created_at": "2024-01-01T12:00:00",
                },
                "summary": {
                    "analysis_summary": "技术面向好，建议持有",
                    "operation_advice": "持有",
                    "trend_prediction": "看多",
                    "sentiment_score": 75,
                    "sentiment_label": "乐观",
                },
                "strategy": {
                    "ideal_buy": "1800.00",
                    "secondary_buy": "1750.00",
                    "stop_loss": "1700.00",
                    "take_profit": "2000.00",
                },
                "details": None,
            }
        }
    )

    meta: ReportMeta = Field(..., description="元信息")
    summary: ReportSummary = Field(..., description="概览区")
    strategy: Optional[ReportStrategy] = Field(None, description="策略点位区")
    details: Optional[ReportDetails] = Field(None, description="详情区")
    conversation: Optional[Any] = Field(None, description="对话记录（prompt + response），仅 conversation 模式")
