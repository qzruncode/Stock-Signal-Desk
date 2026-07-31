# -*- coding: utf-8 -*-
"""Prompt template storage and CRUD operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm.exc import NoResultFound

from src.storage import Base, get_db

DEFAULT_PROMPT_NAME = "综合多维分析"

DEFAULT_PROMPT_CONTENT = """## 角色
你是一个 A股/港股/美股 综合投资分析助手。

## 分析框架
请从以下维度对股票进行全面分析：

### 1. 技术面
- 趋势判断（多头/空头/震荡），关注均线排列和价格位置
- 关键支撑位和压力位
- 成交量和价格的关系（放量/缩量）
- K线形态和短期动能
- 不要迷信单一技术指标，结合多个信号综合判断

### 2. 基本面
- 估值水平（PE/PB 分位数，与同行业对比）
- 财务健康度（营收/利润增速、现金流、ROE）
- 成长性评估
- 行业地位和竞争力

### 3. 资金面
- 主力资金流向（净流入/流出趋势）
- 筹码结构（集中度、获利比例）
- 北向资金/机构持仓变化（如适用）

### 4. 消息面
- 近期重要公告和新闻
- 行业政策动态
- 市场情绪和舆情

### 5. 宏观面
- 大盘指数趋势
- 所属行业板块表现排名
- 相关行业联动

## 输出要求
1. 综合评分（满分100）
2. 各维度分析摘要（每个维度 2-4 句话，给出关键数据）
3. 操作建议：买入/观望/减仓，附止损/目标位
4. 风险提示：列出核心风险点
5. 如果数据不足以做出判断，说明缺少什么数据"""


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    is_default = Column(Integer, default=0)
    created_at = Column(String(30), nullable=False)
    updated_at = Column(String(30), nullable=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromptTemplateStore:
    def __init__(self):
        self._ensure_default()

    def _ensure_default(self):
        existing = self.load_all()
        if not existing:
            self.create(DEFAULT_PROMPT_NAME, DEFAULT_PROMPT_CONTENT, is_default=True)

    def load_all(self) -> List[dict]:
        db = get_db()
        with db._engine.begin() as conn:
            rows = conn.execute(PromptTemplate.__table__.select().order_by(PromptTemplate.created_at)).fetchall()
        return [
            {
                "id": row.id,
                "name": row.name,
                "content": row.content,
                "is_default": bool(row.is_default),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    def get(self, template_id: str) -> Optional[dict]:
        db = get_db()
        with db._engine.begin() as conn:
            row = conn.execute(PromptTemplate.__table__.select().where(PromptTemplate.id == template_id)).fetchone()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "content": row.content,
            "is_default": bool(row.is_default),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def get_default(self) -> Optional[dict]:
        db = get_db()
        with db._engine.begin() as conn:
            row = conn.execute(PromptTemplate.__table__.select().where(PromptTemplate.is_default == 1)).fetchone()
        if row is None:
            return None
        return {
            "id": row.id,
            "name": row.name,
            "content": row.content,
            "is_default": bool(row.is_default),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def create(self, name: str, content: str, is_default: bool = False) -> dict:
        now = _now_iso()
        template_id = str(uuid.uuid4())
        db = get_db()
        with db._engine.begin() as conn:
            conn.execute(
                PromptTemplate.__table__.insert().values(
                    id=template_id,
                    name=name,
                    content=content,
                    is_default=1 if is_default else 0,
                    created_at=now,
                    updated_at=now,
                )
            )
        return self.get(template_id)

    def update(
        self,
        template_id: str,
        name: Optional[str] = None,
        content: Optional[str] = None,
        is_default: Optional[bool] = None,
    ) -> Optional[dict]:
        existing = self.get(template_id)
        if existing is None:
            return None
        db = get_db()
        values = {"updated_at": _now_iso()}
        if name is not None:
            values["name"] = name
        if content is not None:
            values["content"] = content
        if is_default is not None:
            values["is_default"] = 1 if is_default else 0
        with db._engine.begin() as conn:
            conn.execute(PromptTemplate.__table__.update().where(PromptTemplate.id == template_id).values(**values))
        return self.get(template_id)

    def delete(self, template_id: str) -> bool:
        existing = self.get(template_id)
        if existing is None:
            return False
        db = get_db()
        with db._engine.begin() as conn:
            conn.execute(PromptTemplate.__table__.delete().where(PromptTemplate.id == template_id))
        return True


# Cached singleton
_store: Optional[PromptTemplateStore] = None


def get_prompt_template_store() -> PromptTemplateStore:
    global _store
    if _store is None:
        _store = PromptTemplateStore()
    return _store
