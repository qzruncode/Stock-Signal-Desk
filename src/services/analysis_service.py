# -*- coding: utf-8 -*-
"""
===================================
分析服务层
===================================

职责：
1. 封装股票分析逻辑
2. 使用用户提示词模板 + 模型联网搜索执行分析
3. 保存分析结果到数据库
"""

import logging
import uuid
from typing import Optional, Dict, Any, Callable

from src.repositories.analysis_repo import AnalysisRepository

logger = logging.getLogger(__name__)


class _ConversationResult:
    """Minimal result wrapper compatible with save_analysis_history."""

    def __init__(self, code: str, name: str, summary: str):
        self.code = code
        self.name = name
        self.sentiment_score = None
        self.operation_advice = None
        self.trend_prediction = None
        self.analysis_summary = summary
        self.data_sources = ""
        self.raw_response = None

    def get_sniper_points(self) -> Dict[str, Any]:
        return {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "sentiment_score": self.sentiment_score,
            "operation_advice": self.operation_advice,
            "trend_prediction": self.trend_prediction,
            "analysis_summary": self.analysis_summary,
        }


class AnalysisService:
    """
    分析服务

    封装股票分析相关的业务逻辑。
    使用提示词模板作为 system_prompt，让模型自行联网搜索数据。
    """

    def __init__(self):
        self.repo = AnalysisRepository()
        self.last_error: Optional[str] = None

    def analyze_stock(
        self,
        stock_code: str,
        report_type: str = "detailed",
        force_refresh: bool = False,
        query_id: Optional[str] = None,
        send_notification: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        prompt_template_id: Optional[str] = None,
        conversation_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        执行股票分析 — 使用提示词模板 + 模型联网搜索

        Args:
            stock_code: 股票代码
            report_type: 报告类型
            force_refresh: 是否强制刷新
            query_id: 查询 ID（可选）
            send_notification: 是否发送通知
            prompt_template_id: 提示词模板 ID（不传则用默认模板）

        Returns:
            分析结果字典，包含 conversation（prompt + response）
        """
        try:
            self.last_error = None
            from src.config import get_config
            from src.prompt_templates import get_prompt_template_store
            from src.ai_caller import call_ai_for_stock, with_realtime_data_policy
            from src.analyzer import get_stock_name_multi_source

            if query_id is None:
                query_id = uuid.uuid4().hex

            config = get_config()

            # 加载提示词模板
            store = get_prompt_template_store()
            template = None
            if prompt_template_id:
                template = store.get(prompt_template_id)
            if template is None:
                template = store.get_default()

            system_prompt = template.get("content", "") if template else ""
            template_name = template.get("name", "默认模板") if template else "默认模板"

            # 获取股票名称
            stock_name = get_stock_name_multi_source(stock_code)
            effective_system_prompt = with_realtime_data_policy(system_prompt, stock_code, stock_name)

            user_prompt = (
                f"请分析股票 {stock_name}({stock_code})。"
                "必须联网搜索实时最新数据（行情、新闻、公告、财报、资金流向等），"
                "严格遵守系统提示词中的实时数据硬性要求。"
            )
            full_prompt = (
                f"【System Prompt — {template_name}】\n{effective_system_prompt}\n\n"
                f"【User Prompt】\n{user_prompt}"
            )
            conversation = {
                "prompt": full_prompt,
                "response": "",
                "model_used": None,
                "template_name": template_name,
                "template_id": template.get("id") if template else None,
            }
            if conversation_callback:
                conversation_callback(dict(conversation))

            # 创建分析器
            analyzer = self._create_analyzer(config)
            if analyzer is None:
                self.last_error = "无法创建 AI 分析器，请检查模型配置"
                return None

            # 调用 AI（模型自行联网搜索）
            if progress_callback:
                progress_callback(20, f"{stock_name}：正在调用 AI 模型并联网搜索...")

            response_text, model_used, _usage = call_ai_for_stock(
                analyzer,
                system_prompt=effective_system_prompt,
                stock_code=stock_code,
                stock_name=stock_name,
                stream_progress_callback=progress_callback,
            )

            conversation["response"] = response_text
            conversation["model_used"] = model_used
            if conversation_callback:
                conversation_callback(dict(conversation))

            if progress_callback:
                progress_callback(90, f"{stock_name}：分析完成，正在保存结果...")

            # 提取摘要（response 前几条非空行）
            lines = [l for l in response_text.strip().split("\n") if l.strip()]
            brief_summary = "\n".join(lines[:5]) if lines else response_text[:500]

            # 保存到历史记录
            self._save_to_history(
                query_id=query_id,
                stock_code=stock_code,
                stock_name=stock_name,
                conversation=conversation,
                report_type=report_type,
                brief_summary=brief_summary,
            )

            # 发送通知
            if send_notification:
                self._send_notification(stock_code, stock_name, response_text)

            if progress_callback:
                progress_callback(100, f"{stock_name}：分析完成")

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "conversation": conversation,
            }

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"分析股票 {stock_code} 失败: {e}", exc_info=True)
            return None

    def _create_analyzer(self, config):
        """Create a GeminiAnalyzer instance from config."""
        try:
            from src.analyzer import GeminiAnalyzer
            return GeminiAnalyzer(config=config)
        except Exception as e:
            logger.error(f"创建分析器失败: {e}", exc_info=True)
            return None

    def _save_to_history(
        self,
        query_id: str,
        stock_code: str,
        stock_name: str,
        conversation: Dict[str, Any],
        report_type: str,
        brief_summary: str,
    ):
        """Save analysis result to history database."""
        try:
            from src.storage import DatabaseManager

            db = DatabaseManager.get_instance()
            result = _ConversationResult(
                code=stock_code,
                name=stock_name,
                summary=brief_summary[:500] if brief_summary else "",
            )
            # Override raw_result to store the full conversation
            result.raw_response = {"conversation": conversation}

            db.save_analysis_history(
                result=result,
                query_id=query_id,
                report_type="conversation",
                news_content=conversation.get("response", ""),
                context_snapshot=None,
                save_snapshot=False,
            )
        except Exception as e:
            logger.warning(f"保存历史记录失败（非致命）: {e}")

    def _send_notification(self, stock_code: str, stock_name: str, response_text: str):
        """Send push notification for completed analysis."""
        try:
            from src.notification import Notifier
            notifier = Notifier()
            if notifier.is_available():
                preview = response_text[:300].replace("\n", " ").strip()
                notifier.send(
                    f"📊 {stock_name}({stock_code}) 分析完成\n\n{preview}..."
                )
        except Exception as e:
            logger.warning(f"发送通知失败（非致命）: {e}")
