"""Agent-facing custom watchlist group management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.storage import DatabaseManager
from src.tools.base import ToolSpec, object_schema
from src.tools.symbols import resolve_securities_csv


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


def list_watchlist_groups() -> dict[str, Any]:
    """Read persisted custom groups only; default watchlist has its own tool."""
    db = DatabaseManager.get_instance()
    groups = [
        {**item, "count": len(item.get("codes") or [])}
        for item in db.list_watchlist_groups()
    ]
    return _envelope("list", groups=groups, item_count=len(groups))


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


def manage_watchlist_groups(
    action: str,
    group: str = "",
    symbols: str = "",
    new_name: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    """Legacy multiplexed adapter retained for non-Agent callers only."""
    if action == "list":
        return list_watchlist_groups()
    if action == "create":
        return create_watchlist_group(group, symbols)
    if action == "rename":
        return rename_watchlist_group(group, new_name)
    if action == "delete":
        if not confirmed:
            raise ValueError("删除自选分组前必须获得用户明确确认，并传 confirmed=true")
        return delete_watchlist_group(group)
    if action == "add":
        return add_watchlist_group_members(group, symbols)
    if action == "remove":
        return remove_watchlist_group_members(group, symbols)
    raise ValueError("action 必须是 list、create、rename、delete、add 或 remove")


TOOLS = (
    ToolSpec(
        name="list_watchlist_groups",
        description="读取用户创建的自选分组列表；不读取或修改默认自选股。",
        parameters=object_schema(),
        executor=list_watchlist_groups,
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
    "manage_watchlist_groups",
    "remove_watchlist_group_members",
    "rename_watchlist_group",
]
