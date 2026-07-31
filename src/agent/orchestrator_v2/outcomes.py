# -*- coding: utf-8 -*-
"""Convert fixed-executor packets into stable typed task outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    CoverageV2,
    EvidenceV2,
    ErrorDetailV2,
    OutcomeStatus,
    TaskOutcomeV2,
)
from src.agent.task_executor import PlanExecutionResult, TaskExecutionResult


def _coverage_from_packets(
    task: TaskExecutionResult,
) -> CoverageV2:
    packets: list[Mapping[str, Any]] = []
    for call in task.calls:
        if isinstance(call.result, Mapping):
            packets.append(call.result)
    for derived in task.derived_results:
        result = derived.get("result")
        if isinstance(result, Mapping):
            packets.append(result)

    domain_coverages = [
        packet.get("coverage")
        for packet in packets
        if isinstance(packet.get("coverage"), Mapping)
        and {
            "catalog_total",
            "catalog_supplied",
            "selected_count",
            "binding_complete",
        }
        <= set(packet["coverage"])
    ]
    if domain_coverages:
        coverage = domain_coverages[-1]
        requested = max(0, int(coverage.get("catalog_total") or 0))
        binding_complete = coverage.get("binding_complete") is True
        return CoverageV2(
            requested=requested,
            covered=requested if binding_complete else 0,
            missing=() if binding_complete else ("domain_binding",),
            complete=binding_complete,
        )

    if task.task.kind.value == "theme_business_evidence":
        company_packets = [
            packet
            for packet in packets
            if isinstance(packet.get("company_results"), list)
            and packet.get("candidate_scope") == "candidate_collection"
        ]
        if company_packets:
            terminal_symbols = {
                str(item.get("symbol") or "").strip()
                for item in company_packets[-1]["company_results"]
                if isinstance(item, Mapping) and str(item.get("symbol") or "").strip()
            }
            requested_symbols = tuple(dict.fromkeys(task.task.symbols))
            missing = tuple(symbol for symbol in requested_symbols if symbol not in terminal_symbols)
            covered = len(requested_symbols) - len(missing)
            return CoverageV2(
                requested=len(requested_symbols),
                covered=covered,
                missing=missing,
                complete=covered == len(requested_symbols) and not missing,
            )

    requested_values = [
        int(packet.get("requested_count")) for packet in packets if isinstance(packet.get("requested_count"), int)
    ]
    covered_values = [
        int(packet.get("covered_count")) for packet in packets if isinstance(packet.get("covered_count"), int)
    ]
    if task.task.kind.value == "collection_financial_filter" and task.task.symbols:
        groups: dict[tuple[Any, ...], list[int]] = {}
        for call in task.calls:
            if call.tool_name != "get_multi_stock_financials":
                continue
            key = (
                call.arguments.get("metric"),
                call.arguments.get("period_basis"),
                call.arguments.get("fiscal_year"),
            )
            group = groups.setdefault(key, [0, 0])
            group[0] += int(call.result.get("requested_count") or 0)
            group[1] += int(call.result.get("covered_count") or 0)
        requested = len(task.task.symbols)
        covered = min(
            (min(requested, values[1]) for values in groups.values()),
            default=0,
        )
    elif requested_values:
        requested = sum(requested_values)
        covered = min(requested, sum(covered_values))
    else:
        domain_packets = [packet for packet in packets if isinstance(packet.get("requested_domains"), list)]
        requested = (
            len(domain_packets[-1]["requested_domains"])
            if domain_packets
            else len(task.output_entities) if task.output_entities and not task.task.symbols else len(task.task.symbols)
        )
        covered = (
            sum(
                1
                for item in domain_packets[-1].get("domain_results") or []
                if isinstance(item, Mapping) and item.get("success") is True
            )
            if domain_packets
            else len(task.output_entities) if task.output_entities else requested if task.status == "completed" else 0
        )
    missing: list[str] = []
    for packet in packets:
        for key in (
            "missing",
            "missing_symbols",
            "missing_financial_symbols",
            "unresolved_entities",
        ):
            values = packet.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = (
                    str(value.get("symbol") or value.get("input") or "")
                    if isinstance(value, Mapping)
                    else str(value or "")
                ).strip()
                if text and text not in missing:
                    missing.append(text)
    complete = requested == covered and not missing
    return CoverageV2(
        requested=requested,
        covered=covered,
        missing=tuple(missing),
        complete=complete,
    )


def task_outcome_v2(task: TaskExecutionResult) -> TaskOutcomeV2:
    coverage = _coverage_from_packets(task)
    call_packets = [call.result for call in task.calls if isinstance(call.result, Mapping)]
    derived_packets = [
        derived["result"] for derived in task.derived_results if isinstance(derived.get("result"), Mapping)
    ]
    packets = [*call_packets, *derived_packets]
    has_partial_packet = any(packet.get("partial") is True for packet in packets)
    status = {
        "completed": (
            OutcomeStatus.SUCCEEDED if coverage.complete and not has_partial_packet else OutcomeStatus.PARTIAL
        ),
        "failed": OutcomeStatus.FAILED,
        "blocked": OutcomeStatus.BLOCKED,
        "skipped": OutcomeStatus.BLOCKED,
        "cancelled": OutcomeStatus.CANCELLED,
    }.get(task.status, OutcomeStatus.FAILED)
    packet_errors: list[ErrorDetailV2] = []
    packet_error_messages: set[str] = set()
    for packet in packets:
        raw_code = str(packet.get("error_code") or "").strip()
        try:
            error_code = AgentErrorCode(raw_code)
        except ValueError:
            error_code = AgentErrorCode.TOOL_FAILED
        for error in packet.get("errors") or []:
            message = str(error).strip()
            if not message:
                continue
            packet_errors.append(
                ErrorDetailV2(
                    code=error_code,
                    message=message,
                )
            )
            packet_error_messages.add(message)
    task_errors = tuple(
        ErrorDetailV2(
            code=(AgentErrorCode.POLICY_BLOCKED if status == OutcomeStatus.BLOCKED else AgentErrorCode.TOOL_FAILED),
            message=str(error),
        )
        for error in task.errors
        if str(error).strip() not in packet_error_messages
    )
    errors = tuple(dict.fromkeys((*task_errors, *packet_errors)))
    if status == OutcomeStatus.PARTIAL and not coverage.complete:
        errors = (
            *errors,
            ErrorDetailV2(
                code=AgentErrorCode.COVERAGE_INCOMPLETE,
                message=(f"coverage incomplete: {coverage.covered}/" f"{coverage.requested}"),
            ),
        )
    warnings = tuple(
        dict.fromkeys(
            str(warning) for packet in packets for warning in packet.get("warnings") or [] if str(warning).strip()
        )
    )
    evidence: list[EvidenceV2] = []
    for call in task.calls:
        result = call.result if isinstance(call.result, Mapping) else {}
        observed_at = None
        raw_time = result.get("data_time")
        if isinstance(raw_time, str) and raw_time.strip():
            try:
                observed_at = datetime.fromisoformat(raw_time.strip().replace("Z", "+00:00"))
            except ValueError:
                observed_at = None
        evidence.append(
            EvidenceV2(
                source=str(result.get("source") or call.tool_name),
                locator=f"{task.task.task_id}/{call.step_id}",
                observed_at=observed_at,
                summary=(f"{call.tool_name}: " + ("succeeded" if call.success else "failed")),
            )
        )
    for derived in task.derived_results:
        processor = str(derived.get("processor") or "").strip()
        if processor:
            evidence.append(
                EvidenceV2(
                    source=processor,
                    locator=f"{task.task.task_id}/{derived.get('step_id')}",
                    summary="deterministic result processor",
                )
            )
    result_payload = {
        "calls": [call.evidence() for call in task.calls],
        "derived_results": list(task.derived_results),
        "output_entities": [{"symbol": item.symbol, "name": item.name} for item in task.output_entities],
        "resource_outputs": dict(task.resource_outputs),
    }
    return TaskOutcomeV2(
        task_id=task.task.task_id,
        status=status,
        coverage=coverage,
        evidence=tuple(evidence),
        warnings=warnings,
        errors=errors,
        result=result_payload,
    )


def execution_outcomes_v2(
    execution: PlanExecutionResult,
) -> tuple[TaskOutcomeV2, ...]:
    return tuple(task_outcome_v2(task) for task in execution.tasks)


__all__ = [
    "execution_outcomes_v2",
    "task_outcome_v2",
]
