"""Agent-facing custom watchlist group management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.services.watchlist_service import manage_watchlist as _manage_default_watchlist
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


def _is_default_group(identifier: str) -> bool:
    value = str(identifier or "").strip().lower()
    return value in {DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME.lower(), "默认", "默认分组"}


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


def manage_watchlist_groups(
    action: str,
    group: str = "",
    symbols: str = "",
    new_name: str = "",
    confirmed: bool = False,
) -> dict[str, Any]:
    """List or mutate the default/custom watchlist groups used by the product."""
    db = DatabaseManager.get_instance()
    custom_groups = db.list_watchlist_groups()

    if action == "list":
        default = _manage_default_watchlist("list", [])
        groups = [
            {
                "id": DEFAULT_GROUP_ID,
                "name": DEFAULT_GROUP_NAME,
                "codes": list(default.get("codes") or []),
                "count": int(default.get("count") or 0),
                "is_default": True,
                "source": "system_config",
            }
        ]
        groups.extend(
            {
                **item,
                "count": len(item.get("codes") or []),
                "is_default": False,
            }
            for item in custom_groups
        )
        return _envelope("list", groups=groups, item_count=len(groups))

    if action == "create":
        name = str(group or "").strip()
        if not name:
            raise ValueError("创建分组必须提供 group 作为分组名称")
        if _is_default_group(name):
            raise ValueError("默认分组已存在，不能重复创建")
        if any(str(item.get("name") or "") == name for item in custom_groups):
            raise ValueError(f"分组名称已存在: {name}")
        codes = _resolve_symbols(symbols, required=False)
        if codes:
            _manage_default_watchlist("add", codes)
        created = db.upsert_watchlist_group(name, codes, "agent")
        return _envelope(
            "create",
            group={**created, "count": len(created.get("codes") or [])},
            changed=codes,
            message=f"已创建自选分组「{name}」",
        )

    if _is_default_group(group):
        if action in {"rename", "delete"}:
            raise ValueError("默认分组不能重命名或删除")
        if action not in {"add", "remove"}:
            raise ValueError("默认分组只支持 add/remove")
        codes = _resolve_symbols(symbols, required=True)
        result = _manage_default_watchlist(action, codes)
        return _envelope(
            action,
            group={
                "id": DEFAULT_GROUP_ID,
                "name": DEFAULT_GROUP_NAME,
                "codes": list(result.get("codes") or []),
                "count": int(result.get("count") or 0),
                "is_default": True,
            },
            changed=list(result.get("changed") or []),
            message=f"默认自选股已{('添加' if action == 'add' else '移除')} {len(result.get('changed') or [])} 只股票",
        )

    target = _find_custom_group(custom_groups, group)
    target_id = target.get("id")
    target_name = str(target.get("name") or group)

    if action == "rename":
        clean_name = str(new_name or "").strip()
        if not clean_name:
            raise ValueError("重命名必须提供 new_name")
        updated = db.update_watchlist_group(target_id, name=clean_name)
        if updated is None:
            raise ValueError(f"未找到自选分组: {group}")
        return _envelope(
            "rename",
            group={**updated, "count": len(updated.get("codes") or [])},
            message=f"已将自选分组「{target_name}」重命名为「{clean_name}」",
        )

    if action == "delete":
        if not confirmed:
            raise ValueError("删除自选分组前必须获得用户明确确认，并传 confirmed=true")
        if not db.delete_watchlist_group(target_id):
            raise ValueError(f"未找到自选分组: {group}")
        return _envelope(
            "delete",
            deleted=True,
            deleted_group={"id": target_id, "name": target_name},
            message=f"已删除自选分组「{target_name}」",
        )

    if action not in {"add", "remove"}:
        raise ValueError("action 必须是 list、create、rename、delete、add 或 remove")
    codes = _resolve_symbols(symbols, required=True)
    current_codes = list(target.get("codes") or [])
    if action == "add":
        _manage_default_watchlist("add", codes)
        changed = [code for code in codes if code not in current_codes]
        next_codes = [*current_codes, *changed]
    else:
        remove_set = set(codes)
        changed = [code for code in current_codes if code in remove_set]
        next_codes = [code for code in current_codes if code not in remove_set]
    updated = db.update_watchlist_group(target_id, codes=next_codes)
    if updated is None:
        raise ValueError(f"未找到自选分组: {group}")
    return _envelope(
        action,
        group={**updated, "count": len(updated.get("codes") or [])},
        changed=changed,
        message=f"已从自选分组「{target_name}」{('添加' if action == 'add' else '移除')} {len(changed)} 只股票",
    )


TOOL = ToolSpec(
    name="manage_watchlist_groups",
    description=(
        "查看并管理默认自选股及自定义分组。支持列出、创建、重命名、删除分组，以及按名称或代码"
        "批量添加/移除分组成员。只有用户明确要求修改时才能调用；删除分组必须 confirmed=true。"
    ),
    parameters=object_schema(
        {
            "action": {
                "type": "string",
                "enum": ["list", "create", "rename", "delete", "add", "remove"],
            },
            "group": {"type": "string", "description": "分组名称或 ID；create 时为新分组名称"},
            "symbols": {"type": "string", "description": "逗号分隔的股票代码或精确名称"},
            "new_name": {"type": "string", "description": "rename 时的新名称"},
            "confirmed": {"type": "boolean", "default": False},
        },
        required=("action",),
    ),
    executor=manage_watchlist_groups,
    category="action",
)


__all__ = ["TOOL", "manage_watchlist_groups"]
