"""Internal metadata readers for RSS source directories.

The model-visible RSS operation and its source catalogue live solely in
``source_operations``.  These adapters expose only the five upstream metadata
endpoints used by that generic operation.
"""

from __future__ import annotations

from typing import Any

def _picker_result(
    raw: dict[str, Any],
    *,
    provider: str,
    catalog_name: str,
) -> dict[str, Any]:
    error = str(raw.get("_error") or "").strip()
    collections = [value for value in raw.values() if isinstance(value, list)]
    item_count = sum(len(value) for value in collections)
    # Catalog endpoints sometimes expose only their local cache timestamp.
    # Preserve a source-supplied ``data_time`` when present, but never promote
    # transport metadata such as ``_fetched_at`` into evidence time.
    data_time = raw.get("data_time")
    data_time_provenance = raw.get("data_time_provenance")
    if data_time_provenance not in {"source", "inferred", "unavailable"}:
        data_time_provenance = "source" if data_time else "unavailable"
    has_data = item_count > 0
    success = has_data or not error
    return {
        **raw,
        "success": success,
        "partial": bool(error) and has_data,
        "source": {
            "provider": provider,
            "catalog_name": catalog_name,
        },
        "item_count": item_count,
        "data_time": data_time,
        "data_time_provenance": data_time_provenance,
        "data_time_note": (
            raw.get("data_time_note")
            if isinstance(raw.get("data_time_note"), str)
            else (
                None
                if data_time
                else "参数目录未提供原始数据时间；_fetched_at 仅表示本服务获取或缓存刷新时间。"
            )
        ),
        "is_stale": raw.get("is_stale") if data_time else None,
        "freshness_unknown": data_time is None,
        "errors": [error] if error and not has_data else [],
        "warnings": [error] if error and has_data else [],
    }


def list_rss_cih_report_categories(force: bool = False) -> dict[str, Any]:
    from api.v1.endpoints._cih_index_categories import get_cih_index_categories

    return _picker_result(
        get_cih_index_categories(force=bool(force)),
        provider="中指研究院",
        catalog_name="报告分类",
    )


def list_rss_cls_subjects(
    keyword: str = "",
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._cls_subjects import get_cls_subjects

    return _picker_result(
        get_cls_subjects(
            force=bool(force),
            keyword=str(keyword or "").strip() or None,
        ),
        provider="财联社",
        catalog_name="话题目录",
    )


def list_rss_futunn_topics(
    keyword: str = "",
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._futunn_topics import get_futunn_topics

    return _picker_result(
        get_futunn_topics(
            force=bool(force),
            keyword=str(keyword or "").strip() or None,
        ),
        provider="富途牛牛",
        catalog_name="专题目录",
    )


def list_rss_gelonghui_subjects(
    keyword: str = "",
    force: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints._gelonghui_subjects import get_subjects

    return _picker_result(
        get_subjects(
            force=bool(force),
            keyword=str(keyword or "").strip() or None,
        ),
        provider="格隆汇",
        catalog_name="主题目录",
    )


def list_rss_nanhua_report_types(force: bool = False) -> dict[str, Any]:
    from api.v1.endpoints._nanhua_tree import get_nanhua_tree

    return _picker_result(
        get_nanhua_tree(force=bool(force)),
        provider="南华期货",
        catalog_name="研报分类树",
    )


__all__ = [
    "list_rss_cih_report_categories",
    "list_rss_cls_subjects",
    "list_rss_futunn_topics",
    "list_rss_gelonghui_subjects",
    "list_rss_nanhua_report_types",
]
