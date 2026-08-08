# -*- coding: utf-8 -*-
"""Focused tests for evidence views handed to the model."""

from __future__ import annotations

import json

from src.agent.langgraph_runtime.evidence import project_evidence_for_model


def test_projection_preserves_dense_structured_rows_before_omitting_rows() -> None:
    """Long per-row prose must not hide a small requested table from the model."""
    items = [
        {
            "title": f"条目 {index}",
            "source": "示例来源",
            "published": "2026-08-08T00:00:00+00:00",
            "link": f"https://example.test/{index}",
            "summary": "长正文" * 1_000,
        }
        for index in range(30)
    ]
    projected = project_evidence_for_model(
        [
            {
                "evidence_id": "ev_rows",
                "action_id": "read_rows",
                "tool_name": "read_source_rows",
                "success": True,
                "partial": False,
                "entities": {},
                "data_time": "2026-08-08T00:00:00+00:00",
                "data_time_provenance": "source",
                "source_refs": ["tool:read_source_rows"],
                "result": {"success": True, "items": items, "item_count": 30},
            }
        ]
    )

    result = projected[0]["result"]
    rows = result["items"]
    assert len(rows) == 30
    assert [row["title"] for row in rows] == [f"条目 {index}" for index in range(30)]
    assert all(row["source"] == "示例来源" for row in rows)
    assert all(len(row["summary"]) < len(items[0]["summary"]) for row in rows)
    assert len(json.dumps(projected, ensure_ascii=False)) <= 12_000


def test_projection_prioritizes_structured_rows_over_envelope_metadata() -> None:
    """A late row collection must survive a smaller dense-fit mapping budget."""
    rows = [
        {
            "title": f"电报 {index}",
            "source": "示例来源",
            "body": "长正文" * 1_000,
        }
        for index in range(30)
    ]
    result = {
        "success": True,
        **{f"envelope_{index}": f"元数据-{index}" for index in range(20)},
        "records": rows,
    }

    projected = project_evidence_for_model(
        [
            {
                "evidence_id": "ev_late_rows",
                "action_id": "read_late_rows",
                "tool_name": "read_source_rows",
                "success": True,
                "partial": False,
                "entities": {},
                "data_time": None,
                "data_time_provenance": "unavailable",
                "source_refs": ["tool:read_source_rows"],
                "result": result,
            }
        ]
    )

    projected_result = projected[0]["result"]
    assert [row["title"] for row in projected_result["records"]] == [
        f"电报 {index}" for index in range(30)
    ]
    assert all(row["source"] == "示例来源" for row in projected_result["records"])
    assert projected[0]["projection"] == {
        "context_compacted": True,
        "result_omitted": False,
        "collection_rows_omitted": False,
    }
    assert "truncated" not in projected[0]
    assert len(json.dumps(projected, ensure_ascii=False)) <= 12_000
