"""Manage the formal stock-analysis prompt templates."""

from __future__ import annotations

from typing import Any

from src.prompt_templates import get_prompt_template_store
from src.tools._workflow import envelope, require_confirmation
from src.tools.base import ToolSpec, effect_by_argument, object_schema


def manage_analysis_templates(
    action: str,
    template_id: str = "",
    name: str = "",
    content: str = "",
    set_default: bool = False,
    confirmed: bool = False,
) -> dict[str, Any]:
    store = get_prompt_template_store()
    if action == "list":
        items = store.load_all()
        return envelope(action=action, item_count=len(items), items=items)
    if action == "get":
        item = store.get(template_id)
        if item is None:
            raise ValueError("模板不存在")
        return envelope(action=action, template=item)
    if action == "create":
        if not name.strip() or not content.strip():
            raise ValueError("创建模板必须提供 name 和 content")
        if set_default:
            for template in store.load_all():
                if template.get("is_default"):
                    store.update(str(template["id"]), is_default=False)
        item = store.create(name.strip(), content, is_default=bool(set_default))
        return envelope(action=action, template=item)
    if action in {"update", "set_default"}:
        if not template_id:
            raise ValueError("必须提供 template_id")
        make_default = action == "set_default" or bool(set_default)
        if make_default:
            for template in store.load_all():
                if template.get("is_default") and template.get("id") != template_id:
                    store.update(str(template["id"]), is_default=False)
        item = store.update(
            template_id,
            name=name.strip() or None,
            content=content or None,
            is_default=True if make_default else None,
        )
        if item is None:
            raise ValueError("模板不存在")
        return envelope(action=action, template=item)
    if action == "delete":
        require_confirmation(confirmed, "删除分析模板")
        existing = store.get(template_id)
        if existing and existing.get("is_default"):
            raise ValueError("不能直接删除默认模板，请先将其他模板设为默认")
        if not store.delete(template_id):
            raise ValueError("模板不存在")
        return envelope(action=action, template_id=template_id, deleted=True)
    raise ValueError(f"不支持的 action: {action}")


TOOL = ToolSpec(
    name="manage_analysis_templates",
    description=(
        "查看、创建、修改、设为默认或删除正式股票分析模板。只有用户明确要求管理模板时调用；"
        "删除必须 confirmed=true。创建/修改后返回完整模板供用户核对。"
    ),
    parameters=object_schema(
        {
            "action": {"type": "string", "enum": ["list", "get", "create", "update", "set_default", "delete"]},
            "template_id": {"type": "string"},
            "name": {"type": "string"},
            "content": {"type": "string"},
            "set_default": {"type": "boolean", "default": False},
            "confirmed": {"type": "boolean", "default": False},
        },
        required=("action",),
    ),
    executor=manage_analysis_templates,
    category="action",
    effect_resolver=effect_by_argument(
        "action",
        {"create", "update", "set_default", "delete"},
    ),
)


__all__ = ["TOOL", "manage_analysis_templates"]
