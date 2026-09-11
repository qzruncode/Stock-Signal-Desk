"""Agent-facing custom watchlist group management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.watchlist_service import manage_watchlist as _manage_watchlist
from src.storage import DatabaseManager
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


DEFAULT_GROUP_ID = "default"
DEFAULT_GROUP_NAME = "我的自选股"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _resolve_symbols(symbols: str, *, required: bool) -> list[str]:
    raw = str(symbols or "").strip()
    if not raw:
        if required:
            raise ValueError("该操作必须提供 symbols")
        return []
    resolved, unresolved = resolve_securities_csv(raw)
    if unresolved:
        raise ValueError("无法确认股票代码: " + ", ".join(unresolved))
    return list(dict.fromkeys(str(item["symbol"]) for item in resolved))


def _find_custom_group(groups: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    value = str(identifier or "").strip()
    if not value:
        raise ValueError("必须提供 group，用分组名称或分组 ID 指定目标")
    for group in groups:
        if str(group.get("id")) == value or str(group.get("name") or "") == value:
            return group
    raise ValueError(f"未找到自选分组: {value}")


def _envelope(action: str, **payload: Any) -> dict[str, Any]:
    return {
        "success": True,
        "partial": False,
        "action": action,
        **payload,
        "data_time": _now(),
        "is_stale": False,
        "freshness_unknown": False,
        "errors": [],
        "warnings": [],
    }


def _default_group() -> dict[str, Any]:
    result = _manage_watchlist("list")
    codes = [str(code) for code in result.get("codes") or []]
    return {
        "id": DEFAULT_GROUP_ID,
        "name": DEFAULT_GROUP_NAME,
        "codes": codes,
        "source": "system",
    }


def _group_summary(group: dict[str, Any], *, kind: str = "custom") -> dict[str, Any]:
    codes = [str(code) for code in group.get("codes") or []]
    return {
        "id": str(group.get("id") or ""),
        "name": str(group.get("name") or "未命名分组"),
        "count": len(codes),
        "source": str(group.get("source") or "manual"),
        "kind": kind,
    }


def _find_read_group(groups: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    value = str(identifier or "").strip()
    if not value:
        raise ValueError("必须提供 group，用分组名称或分组 ID 指定目标")
    if value.lower() in {DEFAULT_GROUP_ID, "watchlist", DEFAULT_GROUP_NAME.lower()}:
        return _default_group()
    for group in groups:
        if str(group.get("id")) == value or str(group.get("name") or "") == value:
            return {
                **group,
                "id": str(group.get("id") or ""),
                "name": str(group.get("name") or "未命名分组"),
                "codes": [str(code) for code in group.get("codes") or []],
            }
    raise ValueError(f"未找到自选分组: {value}")


def _member_rows(codes: list[str]) -> list[dict[str, str]]:
    try:
        from src.services.name_to_code_resolver import get_database_stock_indexes

        _, code_to_name = get_database_stock_indexes()
    except Exception:
        code_to_name = {}
    return [
        {"symbol": code, "name": str(code_to_name.get(code) or "")}
        for code in codes
    ]


def list_watchlist_groups() -> dict[str, Any]:
    """Read a compact summary of the default and custom stock groups."""
    db = DatabaseManager.get_instance()
    groups = [_group_summary(_default_group(), kind="default")]
    groups.extend(
        _group_summary(item)
        for item in db.list_watchlist_groups()
    )
    return _envelope("list", groups=groups, item_count=len(groups))


def read_watchlist_group(group: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read one stock group and return a bounded page of its members."""
    clean_offset = max(0, int(offset or 0))
    clean_limit = min(100, max(1, int(limit or 100)))
    db = DatabaseManager.get_instance()
    target = _find_read_group(db.list_watchlist_groups(), group)
    codes = list(target.get("codes") or [])
    page = codes[clean_offset : clean_offset + clean_limit]
    next_offset = clean_offset + len(page)
    summary = _group_summary(
        target,
        kind="default" if str(target.get("id")) == DEFAULT_GROUP_ID else "custom",
    )
    return _envelope(
        "read",
        group=summary,
        members=_member_rows(page),
        offset=clean_offset,
        limit=clean_limit,
        returned_count=len(page),
        has_more=next_offset < len(codes),
        next_offset=next_offset if next_offset < len(codes) else None,
        _agent_context={
            "type": "stock_group",
            "group_id": summary["id"],
            "group_name": summary["name"],
            "member_count": summary["count"],
            "source": summary["source"],
        },
    )


def create_watchlist_group(group: str, symbols: str = "") -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    name = str(group or "").strip()
    if not name:
        raise ValueError("创建分组必须提供 group 作为分组名称")
    existing = db.list_watchlist_groups()
    if any(str(item.get("name") or "") == name for item in existing):
        raise ValueError(f"分组名称已存在: {name}")
    codes = _resolve_symbols(symbols, required=False)
    created = db.upsert_watchlist_group(name, codes, "agent")
    return _envelope(
        "create",
        group={**created, "count": len(created.get("codes") or [])},
        changed=codes,
        message=f"已创建自选分组「{name}」",
    )


def rename_watchlist_group(group: str, new_name: str) -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    target = _find_custom_group(db.list_watchlist_groups(), group)
    clean_name = str(new_name or "").strip()
    if not clean_name:
        raise ValueError("重命名必须提供 new_name")
    updated = db.update_watchlist_group(target.get("id"), name=clean_name)
    if updated is None:
        raise ValueError(f"未找到自选分组: {group}")
    target_name = str(target.get("name") or group)
    return _envelope(
        "rename",
        group={**updated, "count": len(updated.get("codes") or [])},
        message=f"已将自选分组「{target_name}」重命名为「{clean_name}」",
    )


def delete_watchlist_group(group: str) -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    target = _find_custom_group(db.list_watchlist_groups(), group)
    if not db.delete_watchlist_group(target.get("id")):
        raise ValueError(f"未找到自选分组: {group}")
    target_name = str(target.get("name") or group)
    return _envelope(
        "delete",
        deleted=True,
        deleted_group={"id": target.get("id"), "name": target_name},
        message=f"已删除自选分组「{target_name}」",
    )


def add_watchlist_group_members(group: str, symbols: str) -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    target = _find_custom_group(db.list_watchlist_groups(), group)
    codes = _resolve_symbols(symbols, required=True)
    current_codes = list(target.get("codes") or [])
    changed = [code for code in codes if code not in current_codes]
    updated = db.update_watchlist_group(target.get("id"), codes=[*current_codes, *changed])
    if updated is None:
        raise ValueError(f"未找到自选分组: {group}")
    target_name = str(target.get("name") or group)
    return _envelope(
        "add",
        group={**updated, "count": len(updated.get("codes") or [])},
        changed=changed,
        message=f"已向自选分组「{target_name}」添加 {len(changed)} 只股票",
    )


def remove_watchlist_group_members(group: str, symbols: str) -> dict[str, Any]:
    db = DatabaseManager.get_instance()
    target = _find_custom_group(db.list_watchlist_groups(), group)
    codes = _resolve_symbols(symbols, required=True)
    current_codes = list(target.get("codes") or [])
    remove_set = set(codes)
    changed = [code for code in current_codes if code in remove_set]
    updated = db.update_watchlist_group(
        target.get("id"),
        codes=[code for code in current_codes if code not in remove_set],
    )
    if updated is None:
        raise ValueError(f"未找到自选分组: {group}")
    target_name = str(target.get("name") or group)
    return _envelope(
        "remove",
        group={**updated, "count": len(updated.get("codes") or [])},
        changed=changed,
        message=f"已从自选分组「{target_name}」移除 {len(changed)} 只股票",
    )


TOOLS = (
    ToolSpec(
        name="list_watchlist_groups",
        description="读取默认自选股和用户创建的自选分组摘要；返回分组名称、ID和股票数量，不修改数据。",
        parameters=object_schema(),
        executor=list_watchlist_groups,
        category="action",
    ),
    ToolSpec(
        name="read_watchlist_group",
        description="读取一个指定自选分组中的股票代码；支持按分组名称或 ID 分页读取，不修改数据。",
        parameters=object_schema(
            {
                "group": {"type": "string", "description": "分组名称或 ID；默认自选股可用 default 或 我的自选股"},
                "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "成员起始位置"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100, "description": "本次最多读取的成员数量"},
            },
            required=("group",),
        ),
        executor=read_watchlist_group,
        category="action",
    ),
    ToolSpec(
        name="create_watchlist_group",
        description="创建一个独立自选股分组，可在创建时写入明确指定的成员；不会修改默认自选股，必须经过用户审批。",
        parameters=object_schema(
            {
                "group": {"type": "string", "description": "新分组名称"},
                "symbols": {"type": "string", "description": "可选的逗号分隔股票代码或名称"},
            },
            required=("group",),
        ),
        executor=create_watchlist_group,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="rename_watchlist_group",
        description="重命名一个已有自选股分组；必须经过用户审批。",
        parameters=object_schema(
            {
                "group": {"type": "string", "description": "现有分组名称或 ID"},
                "new_name": {"type": "string", "description": "新的分组名称"},
            },
            required=("group", "new_name"),
        ),
        executor=rename_watchlist_group,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="delete_watchlist_group",
        description="删除一个已有自选股分组；必须经过用户审批。",
        parameters=object_schema(
            {"group": {"type": "string", "description": "要删除的分组名称或 ID"}},
            required=("group",),
        ),
        executor=delete_watchlist_group,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="add_watchlist_group_members",
        description="向一个已有自选股分组加入明确指定的成员；必须经过用户审批。",
        parameters=object_schema(
            {
                "group": {"type": "string", "description": "分组名称或 ID"},
                "symbols": {"type": "string", "description": "逗号分隔的股票代码或精确名称"},
            },
            required=("group", "symbols"),
        ),
        executor=add_watchlist_group_members,
        category="action",
        effect="side_effect",
    ),
    ToolSpec(
        name="remove_watchlist_group_members",
        description="从一个已有自选股分组移除明确指定的成员；必须经过用户审批。",
        parameters=object_schema(
            {
                "group": {"type": "string", "description": "分组名称或 ID"},
                "symbols": {"type": "string", "description": "逗号分隔的股票代码或精确名称"},
            },
            required=("group", "symbols"),
        ),
        executor=remove_watchlist_group_members,
        category="action",
        effect="side_effect",
    ),
)


__all__ = [
    "TOOLS",
    "add_watchlist_group_members",
    "create_watchlist_group",
    "delete_watchlist_group",
    "list_watchlist_groups",
    "remove_watchlist_group_members",
    "rename_watchlist_group",
]
