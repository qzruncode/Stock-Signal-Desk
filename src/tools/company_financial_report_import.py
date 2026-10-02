"""Official exchange filing lookup and approval-gated PDF import into RAG."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from src.services.company_report_service import (
    CompanyReportError,
    existing_company_financial_report,
    existing_report_result,
    find_company_financial_reports as _find_reports,
    import_company_financial_report as _import_report,
)
from src.services.rag_knowledge_base_service import RagKnowledgeBaseService
from src.tools.base import (
    ToolSpec,
    current_tool_effect_approval,
    current_tool_execution_context,
    object_schema,
)


def _active_selected_knowledge_bases(context: dict[str, Any]) -> list[dict[str, Any]]:
    selected_ids = [
        value.strip()
        for value in str(context.get("knowledge_base_ids") or "").split(",")
        if value.strip()
    ]
    if not selected_ids:
        return []
    all_bases = RagKnowledgeBaseService().list_knowledge_bases(
        tenant_id=str(context.get("tenant_id") or "local"),
        owner_id=str(context.get("owner_id") or "admin"),
    )
    return [item for item in all_bases if item.get("id") in selected_ids and item.get("status") == "active"]


def _selected_ids(context: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        value.strip()
        for value in str(context.get("knowledge_base_ids") or "").split(",")
        if value.strip()
    ))


def _tool_error(exc: CompanyReportError) -> dict[str, Any]:
    return {
        "success": False,
        "error_code": exc.code,
        "retryable": exc.retryable,
        "message": str(exc),
        "errors": [str(exc)],
        "warnings": [],
    }


def _existing_report(arguments: Mapping[str, Any]) -> dict[str, Any] | None:
    context = current_tool_execution_context()
    selected_ids = _selected_ids(context)
    if len(selected_ids) != 1 or not all(arguments.get(key) for key in ("company", "candidate_id", "report_title")):
        return None
    return existing_company_financial_report(
        company_query=str(arguments["company"]), candidate_id=str(arguments["candidate_id"]),
        report_title=str(arguments["report_title"]), knowledge_base_id=selected_ids[0],
        tenant_id=str(context.get("tenant_id") or "local"),
        owner_id=str(context.get("owner_id") or "admin"),
        kb_service=RagKnowledgeBaseService(),
    )


def _import_effect(arguments: Mapping[str, Any]) -> str:
    # Existing ToolSpec supports argument-dependent effects. Only a verified
    # local reuse is read-only; missing/unverified files still need approval.
    return "read" if _existing_report(arguments) is not None else "side_effect"


class ImportCompanyFinancialReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=80, description="与候选检索相同的公司名或代码")
    candidate_id: str = Field(pattern=r"^report_[a-f0-9]{32}$", description="候选列表中的 candidate_id，不是 evidence_id 或 tool_call_id")
    report_title: str = Field(min_length=1, max_length=500, description="候选列表中完整的报告标题，用于审批确认")

    @model_validator(mode="after")
    def validate_discovered_candidate(self, info: ValidationInfo) -> "ImportCompanyFinancialReportArgs":
        if info.context is None:
            return self
        if len(_selected_ids(current_tool_execution_context())) != 1:
            raise ValueError("导入前请只选择一个目标知识库；没有请求审批。")
        for record in info.context.get("tool_results", []):
            if record.get("tool_name") != "search_company_financial_reports" or record.get("success") is not True:
                continue
            result = record.get("result") or {}
            company = result.get("company") or {}
            if self.company not in {company.get("code"), company.get("name"), (record.get("arguments") or {}).get("company")}:
                continue
            if any(candidate.get("candidate_id") == self.candidate_id and candidate.get("title") == self.report_title
                   for candidate in result.get("candidates", [])):
                return self
        if _existing_report(self.model_dump()) is not None:
            return self
        raise ValueError("candidate_id、report_title、company 必须原样对应本轮 search_company_financial_reports 的真实候选；不得使用证据或工具调用编号。")


def search_company_financial_reports(
    company: str,
    report_type: str = "latest",
) -> dict[str, Any]:
    """Find full periodic-report PDFs on the issuer's exchange disclosure source."""
    try:
        result = _find_reports(company, report_type)
    except CompanyReportError as exc:
        return _tool_error(exc)
    except Exception:
        return {
            "success": False,
            "error_code": "report_search_failed",
            "retryable": True,
            "message": "官方财报检索失败；未改用二手公告源。",
            "errors": ["官方财报检索暂不可用。"],
            "warnings": [],
        }

    context = current_tool_execution_context()
    selected_ids = _selected_ids(context)
    try:
        selected_bases = _active_selected_knowledge_bases(context) if selected_ids else []
    except Exception:
        selected_bases = None
    if selected_bases is None:
        result["import_target"] = None
        result["import_block_reason"] = "暂时无法核实知识库状态，请稍后重试；没有开始导入。"
    elif len(selected_ids) == 1 and len(selected_bases) == 1:
        result["import_target"] = selected_bases[0]["name"]
        result["import_block_reason"] = None
        for candidate in result["candidates"]:
            try:
                existing = _existing_report({
                    "company": (result.get("company") or {}).get("code") or company,
                    "candidate_id": candidate.get("candidate_id"), "report_title": candidate.get("title"),
                })
            except Exception:
                candidate["already_in_knowledge_base"] = None
                result["import_block_reason"] = "无法核实目标库中是否已有原件；不能据此断言报告未入库。"
                continue
            candidate["already_in_knowledge_base"] = existing is not None
            candidate["knowledge_base_document"] = (
                {"filename": existing.get("filename"), "status": existing.get("status"),
                 "searchable": bool(existing.get("active_index_version_id"))}
                if existing is not None else None
            )
    elif not selected_ids:
        result["import_target"] = None
        result["import_block_reason"] = "请先在对话框左下角选择一个知识库。"
    elif len(selected_ids) != 1:
        result["import_target"] = None
        result["import_block_reason"] = "导入前请只保留一个目标知识库。"
    else:
        result["import_target"] = None
        result["import_block_reason"] = "当前选择的知识库不可用，请刷新后重新选择。"
    if not result["candidates"]:
        result["message"] = "官方来源未找到符合条件的完整财务报告 PDF。"
    return result


def import_company_financial_report(
    company: str,
    candidate_id: str,
    report_title: str,
) -> dict[str, Any]:
    """Import one freshly revalidated official PDF into the selected knowledge base."""
    try:
        existing = _existing_report({"company": company, "candidate_id": candidate_id, "report_title": report_title})
    except Exception:
        return _tool_error(CompanyReportError(
            "无法核实目标知识库的原件状态，没有开始下载或写入。",
            code="knowledge_base_scope_unavailable", retryable=True,
        ))
    if existing is not None:
        return existing_report_result(existing)
    if not current_tool_effect_approval():
        return {
            "success": False,
            "error_code": "approval_required",
            "retryable": False,
            "message": "导入原始财报需要用户审批。",
            "errors": ["导入操作尚未获批。"],
            "warnings": [],
        }
    context = current_tool_execution_context()
    selected_ids = _selected_ids(context)
    try:
        selected_bases = _active_selected_knowledge_bases(context) if selected_ids else []
    except Exception:
        selected_bases = None
    if selected_bases is None:
        return {
            "success": False,
            "error_code": "knowledge_base_scope_unavailable",
            "retryable": True,
            "message": "无法确认目标知识库状态，未开始下载。",
            "errors": ["知识库状态暂不可用。"],
            "warnings": [],
        }
    if len(selected_ids) != 1 or len(selected_bases) != 1:
        reason = "请先选择且只选择一个可用知识库，再发起原始 PDF 导入。"
        return {
            "success": False,
            "error_code": "knowledge_base_scope_required",
            "retryable": False,
            "message": reason,
            "errors": [reason],
            "warnings": [],
        }
    try:
        return _import_report(
            company_query=company,
            candidate_id=candidate_id,
            report_title=report_title,
            knowledge_base_id=str(selected_bases[0]["id"]),
            tenant_id=str(context.get("tenant_id") or "local"),
            owner_id=str(context.get("owner_id") or "admin"),
        )
    except CompanyReportError as exc:
        return _tool_error(exc)
    except Exception as exc:
        return {
            "success": False,
            "error_code": str(getattr(exc, "code", "report_import_failed")),
            "retryable": bool(getattr(exc, "retryable", False)),
            "message": "官方 PDF 未能完成知识库入库；未保存不完整文件，可以重新检索后重试。",
            "errors": ["PDF 未能完成知识库入库。"],
            "warnings": [],
        }


TOOLS = (
    ToolSpec(
        name="search_company_financial_reports",
        description=(
            "从上交所/深交所 RSSHub 正式公告路由，或北交所官方巨潮定期报告接口，查找指定 A 股公司的完整财务报告 PDF。"
            "用户说‘最近一期/最新财报’时使用 report_type=latest；只返回正式报告原件，不选摘要或英文版。"
            "结果按财务报告期排序，必须从 candidates 中选 candidate_id 与原样 report_title。"
            "检索结果会指出当前对话的知识库导入条件；未选择唯一知识库时先请用户在对话框左下角选择。"
            "already_in_knowledge_base=true 表示同一报告已存在；不要重导入，直接按需 search_knowledge_base，不能将没有阻断条件误读成报告尚未入库。"
            "若用户已明确要求下载/入库且候选列表非空，应使用 recommended_candidate_id 与对应原样标题调用导入工具。"
            "官方来源不可用时明确报告失败，不得改用 AKShare、东方财富或任意网页。"
        ),
        parameters=object_schema(
            {
                "company": {"type": "string", "minLength": 1, "maxLength": 80, "description": "精确上市公司名称或六位证券代码"},
                "report_type": {
                    "type": "string",
                    "enum": ["latest", "annual", "semiannual", "quarterly"],
                    "default": "latest",
                    "description": "latest=财务期间最新；其余为年报、半年报或一/三季报",
                },
            },
            required=("company",),
        ),
        executor=search_company_financial_reports,
        category="events",
        timeout_seconds=90,
        max_attempts=1,
        web_fallback=False,
    ),
    ToolSpec(
        name="import_company_financial_report",
        description=(
            "将 search_company_financial_reports 返回的某一份官方 PDF 原件导入本轮唯一选中的知识库。"
            "只能传回候选中的 candidate_id、原样 report_title 和同一 company；不得提供 URL。"
            "这是有副作用的持久化操作，执行前必须经过用户审批；下载会重新从官方来源校验候选并限制为交易所 PDF 域名。"
            "若服务端确认同一官方原件已在所选库中，仅返回现有文档状态，不下载、不写入、不请求审批。"
        ),
        parameters=None,
        args_model=ImportCompanyFinancialReportArgs,
        executor=import_company_financial_report,
        category="action",
        effect="side_effect",
        effect_resolver=_import_effect,
        timeout_seconds=180,
        max_attempts=2,
        idempotent=True,
        web_fallback=False,
    ),
)


__all__ = [
    "TOOLS",
    "import_company_financial_report",
    "search_company_financial_reports",
]
