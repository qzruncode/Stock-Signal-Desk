"""Function group 1 extracted from src/agent/result_contracts.py."""

from __future__ import annotations

from src.agent.result_contracts import (
    dataclass,
    Any,
    Iterable,
    Literal,
    Mapping,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
    AnalysisPlaybook,
    INDUSTRY_CHAIN,
    MARKET_OUTLOOK,
    THEME_COMPANY_MAPPING,
    INVESTMENT_DECISION,
    STOCK_DEEP_RESEARCH,
    FinancialFilterCondition,
    CollectionFinancialFilterSpec,
    DomainBoardQuerySpec,
    IndustryBenefitRoleV2,
    IndustryBenefitOutlineV2,
    DomainCatalogSelectionItemV2,
    DomainCatalogSelectionV2,
    DomainResultSelectionV2,
    DomainSelectionAssumptionV2,
    DomainCollectionCoverageV2,
    DomainBoardBindingV2,
    DomainCollectionV2,
    InvestmentThesisContext,
    ThemeDomainThesis,
    ThemeEvidenceContext,
    MappingSelectionContext,
    __all__,
 )

__all__ = ['_financial_filter_spec_for_projection', 'project_collection_financial_filter_entities', 'project_task_output_entities']

def _financial_filter_spec_for_projection(
    parameters: Mapping[str, Any],
) -> CollectionFinancialFilterSpec:
    """Read current contracts and migrate persisted v1 single-condition state."""
    try:
        return CollectionFinancialFilterSpec.model_validate(parameters)
    except Exception:
        legacy_keys = {
            "metric",
            "period_basis",
            "fiscal_year",
            "operator",
            "threshold",
            "threshold_unit",
            "action",
        }
        legacy = {key: value for key, value in parameters.items() if key in legacy_keys}
        return CollectionFinancialFilterSpec(conditions=[FinancialFilterCondition.model_validate(legacy)])

def project_collection_financial_filter_entities(
    input_entities: Iterable[Mapping[str, Any]],
    result_context: Iterable[Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Project the exact retained collection from typed financial results.

    The rendered Markdown is deliberately irrelevant here.  A follow-up such
    as "这些里面哪些能买" must inherit the program-computed output set, not
    the input set and not a sample copied by the planning model.
    """
    spec = _financial_filter_spec_for_projection(parameters)
    ordered = [
        {
            "symbol": str(item.get("symbol") or "").strip(),
            "name": str(item.get("name") or item.get("symbol") or "").strip(),
        }
        for item in input_entities
        if str(item.get("symbol") or "").strip()
    ]
    rows_by_condition: dict[
        tuple[str, str, int | None],
        dict[str, Mapping[str, Any]],
    ] = {condition.identity: {} for condition in spec.conditions}
    for packet in result_context:
        if not isinstance(packet, Mapping):
            continue
        arguments = packet.get("arguments") if isinstance(packet.get("arguments"), Mapping) else {}
        result = packet.get("result") if isinstance(packet.get("result"), Mapping) else packet
        if not isinstance(result, Mapping) or result.get("success") is False:
            continue
        for condition in spec.conditions:
            if arguments and any(
                (
                    arguments.get("metric") != condition.metric,
                    arguments.get("period_basis") != condition.period_basis,
                    arguments.get("fiscal_year") != condition.fiscal_year,
                )
            ):
                continue
            rows = rows_by_condition[condition.identity]
            for item in result.get("items") or []:
                if not isinstance(item, Mapping):
                    continue
                if item.get("metric") not in (None, condition.metric):
                    continue
                if item.get("period_basis") not in (None, condition.period_basis):
                    continue
                symbol = str(item.get("symbol") or "").strip()
                if symbol and isinstance(item.get("financial_value"), (int, float)):
                    rows[symbol] = item

    # An incomplete transform has no authoritative output collection.  Failing
    # closed here prevents a partial batch from silently becoming the next
    # conversational universe.
    if any(
        entity["symbol"] not in rows_by_condition[condition.identity]
        for condition in spec.conditions
        for entity in ordered
    ):
        return []

    retained: list[dict[str, str]] = []
    for entity in ordered:
        if not all(
            condition.keeps(float(rows_by_condition[condition.identity][entity["symbol"]]["financial_value"]))
            for condition in spec.conditions
        ):
            continue
        first_row = rows_by_condition[spec.conditions[0].identity][entity["symbol"]]
        retained.append(
            {
                "symbol": entity["symbol"],
                "name": str(first_row.get("name") or entity["name"]).strip(),
            }
        )
    return retained

def project_task_output_entities(
    kind: str,
    input_entities: Iterable[Mapping[str, Any]],
    result_context: Iterable[Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return the task's typed output collection according to its contract."""
    if kind == "collection_financial_filter":
        return project_collection_financial_filter_entities(
            input_entities,
            result_context,
            parameters,
        )

    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            symbol = str(value.get("symbol") or value.get("code") or value.get("stock_code") or "").strip()
            if len(symbol) == 6 and symbol.isdigit() and symbol not in seen:
                seen.add(symbol)
                found.append(
                    {
                        "symbol": symbol,
                        "name": str(value.get("name") or value.get("stock_name") or symbol).strip(),
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for result in result_context:
        visit(result)
    if found:
        return found
    return [
        {
            "symbol": str(item.get("symbol") or "").strip(),
            "name": str(item.get("name") or item.get("symbol") or "").strip(),
        }
        for item in input_entities
        if str(item.get("symbol") or "").strip()
    ]
