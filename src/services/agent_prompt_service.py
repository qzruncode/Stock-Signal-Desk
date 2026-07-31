# -*- coding: utf-8 -*-
"""Agent system prompt 模板管理服务。

负责对 `agent_prompt_templates` 表做 CRUD 与生效切换，并提供
`get_active_system_prompt()` 供 chat 端点读取当前生效的 system prompt。

回落策略：DB 无生效模板、生效模板 content 为空、或查询异常时，
回落到 `api/v1.endpoints.agent.chat.SYSTEM_PROMPT` 源码常量，保证开箱即用。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# 模块级缓存：避免标准任务流水线每次请求都查 DB。
# 缓存值为 (content, is_fallback)；写入操作（create/update/delete/set_active）触发失效。
# 不缓存异常回退路径，避免把瞬时错误固化。
_cached_active_prompt: Optional[Tuple[str, bool]] = None


class AgentPromptService:
    """AI 助手 system prompt 模板管理服务。"""

    DEFAULT_TEMPLATE_NAME = "系统默认"

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ---- CRUD ----

    def list_templates(self) -> List[Dict[str, Any]]:
        records = self.db.list_agent_prompts()
        return [r.to_dict() for r in records]

    def get_template(self, template_id: int) -> Optional[Dict[str, Any]]:
        record = self.db.get_agent_prompt(template_id)
        return record.to_dict() if record else None

    def create_template(
        self,
        name: str,
        content: str,
        is_active: bool = False,
    ) -> Dict[str, Any]:
        record = self.db.create_agent_prompt(name=name, content=content, is_active=is_active)
        if is_active:
            # 新建即生效：先清空缓存，再保证唯一 active
            self._invalidate_cache()
            self.db.set_agent_prompt_active(record.id)
            record = self.db.get_agent_prompt(record.id) or record
        self._invalidate_cache()
        return record.to_dict()

    def update_template(
        self,
        template_id: int,
        name: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        record = self.db.update_agent_prompt(template_id, name=name, content=content)
        if record is None:
            return None
        self._invalidate_cache()
        return record.to_dict()

    def delete_template(self, template_id: int) -> bool:
        ok = self.db.delete_agent_prompt(template_id)
        if ok:
            self._invalidate_cache()
        return ok

    def set_active(self, template_id: int) -> Optional[Dict[str, Any]]:
        record = self.db.set_agent_prompt_active(template_id)
        if record is None:
            return None
        self._invalidate_cache()
        return record.to_dict()

    # ---- 读取生效 prompt ----

    def get_active_system_prompt(self) -> Tuple[str, bool]:
        """返回 (content, is_fallback)。

        优先返回模块级缓存；缓存未命中时查 DB 当前生效模板；
        DB 无模板 / content 为空 / 查询异常 → 回落源码常量 SYSTEM_PROMPT。
        """
        global _cached_active_prompt
        if _cached_active_prompt is not None:
            return _cached_active_prompt

        content, is_fallback = self._resolve_active_prompt()
        if not is_fallback:
            # 只缓存正常取到的生效 prompt，不缓存回退值（回退可能在下次写入后失效）
            _cached_active_prompt = (content, is_fallback)
        return content, is_fallback

    def _resolve_active_prompt(self) -> Tuple[str, bool]:
        try:
            record = self.db.get_active_agent_prompt()
        except Exception as exc:
            logger.warning("[AgentPrompt] 读取生效 prompt 失败，回落源码默认: %s", exc)
            return self._fallback_prompt(), True

        if record is None or not (record.content or "").strip():
            return self._fallback_prompt(), True

        # “系统默认”由代码版本管理。工具契约或证据规则升级后自动同步，
        # 避免数据库里首次种下的旧 prompt 永久覆盖新能力；用户自建/改名模板不动。
        if record.name == self.DEFAULT_TEMPLATE_NAME:
            current = self._fallback_prompt()
            if record.content != current:
                try:
                    self.db.update_agent_prompt(record.id, content=current)
                except Exception as exc:
                    logger.warning("[AgentPrompt] 同步系统默认 prompt 失败，当前请求仍使用新版: %s", exc)
            return current, False

        return record.content, False

    @staticmethod
    def _fallback_prompt() -> str:
        """回落到 chat.py 的 SYSTEM_PROMPT 源码常量。"""
        try:
            from api.v1.endpoints.agent.chat import SYSTEM_PROMPT

            return SYSTEM_PROMPT
        except Exception as exc:  # pragma: no cover - 极端兜底
            logger.error("[AgentPrompt] 回落 SYSTEM_PROMPT 失败: %s", exc)
            return "你是 A 股智能分析助手。"

    @staticmethod
    def _invalidate_cache() -> None:
        global _cached_active_prompt
        _cached_active_prompt = None

    # ---- 首次种子 ----

    def ensure_default_template(self) -> Optional[Dict[str, Any]]:
        """表为空时，用源码 SYSTEM_PROMPT 种一条生效的默认模板。

        让设置页首次进入就能看到并编辑当前 prompt，而非面对空列表。
        表已有数据则不做任何事。
        """
        existing = self.db.list_agent_prompts()
        if existing:
            return None
        fallback = self._fallback_prompt()
        record = self.db.create_agent_prompt(
            name=self.DEFAULT_TEMPLATE_NAME,
            content=fallback,
            is_active=True,
        )
        self._invalidate_cache()
        return record.to_dict()
