"""Manage the formal stock-analysis prompt templates."""

from __future__ import annotations

from typing import Any

from src.prompt_templates import get_prompt_template_store
from src.tools._workflow import envelope, require_confirmation
from src.tools.base import ToolSpec, object_schema


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


def list_analysis_templates() -> dict[str, Any]:
    items = get_prompt_template_store().load_all()
    return envelope(action="list", item_count=len(items), items=items)


def read_analysis_template(template_id: str) -> dict[str, Any]:
    item = get_prompt_template_store().get(template_id)
    if item is None:
        raise ValueError("模板不存在")
    return envelope(action="get", template=item)


def create_analysis_template(
    name: str,
    content: str,
) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    clean_content = str(content or "").strip()
    if not clean_name or not clean_content:
        raise ValueError("创建模板必须提供 name 和 content")
    item = get_prompt_template_store().create(
        clean_name,
        clean_content,
        # Creating a template and changing the active default are separate
        # effects.  The next action can explicitly call set_default.
        is_default=False,
    )
    return envelope(action="create", template=item)


def update_analysis_template(
    template_id: str,
    name: str = "",
    content: str = "",
) -> dict[str, Any]:
    if not str(name or "").strip() and not str(content or "").strip():
        raise ValueError("更新模板至少需要提供 name 或 content")
    item = get_prompt_template_store().update(
        template_id,
        name=str(name or "").strip() or None,
        content=str(content or "") or None,
    )
    if item is None:
        raise ValueError("模板不存在")
    return envelope(action="update", template=item)


def set_default_analysis_template(template_id: str) -> dict[str, Any]:
    item = get_prompt_template_store().set_default(template_id)
    if item is None:
        raise ValueError("模板不存在")
    return envelope(action="set_default", template=item)


def delete_analysis_template(template_id: str) -> dict[str, Any]:
    if not get_prompt_template_store().delete_non_default(template_id):
        raise ValueError("模板不存在或当前为默认模板；请先明确设置其他默认模板")
    return envelope(action="delete", template_id=template_id, deleted=True)


TOOLS = (
    ToolSpec(
        name="list_analysis_templates",
        description="读取可用的分析模板列表；不修改模板。",
        parameters=object_schema(),
        executor=list_analysis_templates,
        category="action",
    ),
    ToolSpec(
        name="read_analysis_template",
        description="读取一个指定分析模板的完整内容；不修改模板。",
        parameters=object_schema(
            {"template_id": {"type": "string", "description": "模板 ID"}},
            required=("template_id",),
        ),
        executor=read_analysis_template,
        category="action",
    ),
    ToolSpec(
        name="create_analysis_template",
        description="创建一个分析模板；不会同时改动默认模板，必须经过用户审批。",
        parameters=object_schema(
            {
                "name": {"type": "string", "description": "模板名称"},
                "content": {"type": "string", "description": "模板正文"},
            },
            required=("name", "content"),
        ),
        executor=create_analysis_template,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="update_analysis_template",
        description="更新一个已有分析模板的名称或正文；必须经过用户审批。",
        parameters=object_schema(
            {
                "template_id": {"type": "string", "description": "模板 ID"},
                "name": {"type": "string", "description": "可选的新名称"},
                "content": {"type": "string", "description": "可选的新正文"},
            },
            required=("template_id",),
        ),
        executor=update_analysis_template,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="set_default_analysis_template",
        description="将一个已有分析模板设为默认模板；必须经过用户审批。",
        parameters=object_schema(
            {"template_id": {"type": "string", "description": "模板 ID"}},
            required=("template_id",),
        ),
        executor=set_default_analysis_template,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="delete_analysis_template",
        description="删除一个非默认分析模板；必须经过用户审批。",
        parameters=object_schema(
            {"template_id": {"type": "string", "description": "模板 ID"}},
            required=("template_id",),
        ),
        executor=delete_analysis_template,
        category="action",
        effect="side_effect",
    ),
)


__all__ = [
    "TOOLS",
    "create_analysis_template",
    "delete_analysis_template",
    "list_analysis_templates",
    "manage_analysis_templates",
    "read_analysis_template",
    "set_default_analysis_template",
    "update_analysis_template",
]
