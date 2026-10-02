from __future__ import annotations

from unittest.mock import patch

from src.tools.base import tool_effect_approval, tool_execution_context
from src.tools.company_financial_report_import import (
    import_company_financial_report,
    search_company_financial_reports,
)
from src.tools.registry import ToolRegistry


def test_report_search_reports_current_knowledge_base_import_readiness() -> None:
    result = {
        "success": True,
        "company": {"code": "300850", "name": "新强联"},
        "candidates": [{"candidate_id": "report_" + "a" * 32}],
    }
    with (
        patch("src.tools.company_financial_report_import._find_reports", return_value=result),
        patch("src.tools.company_financial_report_import.RagKnowledgeBaseService") as rag_service,
        tool_execution_context(knowledge_base_ids=("kb-1",)),
    ):
        rag_service.return_value.list_knowledge_bases.return_value = [
            {"id": "kb-1", "name": "财报库", "status": "active"}
        ]
        response = search_company_financial_reports("新强联")

    assert response["import_target"] == "财报库"
    assert response["import_block_reason"] is None


def test_report_search_blocks_import_when_chat_has_no_selected_knowledge_base() -> None:
    result = {"success": True, "company": {}, "candidates": []}
    with (
        patch("src.tools.company_financial_report_import._find_reports", return_value=result),
        tool_execution_context(knowledge_base_ids=()),
    ):
        response = search_company_financial_reports("新强联")

    assert "左下角" in response["import_block_reason"]


def test_import_tool_checks_server_approval_before_external_or_database_work() -> None:
    with (
        tool_execution_context(knowledge_base_ids=("kb-1",)),
        tool_effect_approval(False),
        patch("src.tools.company_financial_report_import._existing_report", return_value=None),
        patch("src.tools.company_financial_report_import._import_report") as import_report,
    ):
        response = import_company_financial_report(
            "新强联", "report_" + "a" * 32, "新强联：2026年半年度报告"
        )

    assert response["error_code"] == "approval_required"
    import_report.assert_not_called()


def test_import_tool_uses_only_the_server_selected_single_knowledge_base() -> None:
    expected = {"success": True, "document": {"id": "doc-1"}}
    with (
        tool_execution_context(knowledge_base_ids=("kb-selected",)),
        tool_effect_approval(True),
        patch("src.tools.company_financial_report_import._existing_report", return_value=None),
        patch("src.tools.company_financial_report_import._active_selected_knowledge_bases", return_value=[{"id": "kb-selected", "name": "财报库"}]),
        patch("src.tools.company_financial_report_import._import_report", return_value=expected) as importer,
    ):
        response = import_company_financial_report(
            "新强联", "report_" + "a" * 32, "新强联：2026年半年度报告"
        )

    assert response == expected
    assert importer.call_args.kwargs["knowledge_base_id"] == "kb-selected"


def test_report_tools_are_registered_with_import_as_an_approved_side_effect() -> None:
    registry = ToolRegistry()
    search = registry.get_tool("search_company_financial_reports")
    importer = registry.get_tool("import_company_financial_report")

    assert search is not None and importer is not None
    assert search.effect == "read"
    assert importer.effect == "side_effect"
    assert "knowledge_base_id" not in importer.parameters["properties"]
    assert "url" not in importer.parameters["properties"]
