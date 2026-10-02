"""Capability scoping and execution identity for team workers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re
from typing import Any

from src.tools.registry import ToolRegistry

from ..executor import execution_event_scope


_COMMON_READ_TOOLS = frozenset({"search_stocks"})

# Module ownership is the stable authority in production.  Explicit names are
# needed for ``source_operations`` because that module intentionally exposes
# several distinct capabilities behind one module-owned registry.
_ROLE_OWNER_MODULES: dict[str, frozenset[str]] = {
    "market": frozenset(
        {
            "market_snapshot_tools",
            "get_sector_flow",
            "get_stock_capital_flow",
            "get_index_data",
            "get_bond_yield",
            "get_macro_indicator",
            "screen_atr_volatility_stocks",
        }
    ),
    "fundamental": frozenset(
        {
            "get_multi_stock_financials",
            "get_stock_info",
            "get_financials",
            "get_balance_sheet",
            "get_income_statement",
            "get_cashflow",
            "get_business_segments",
            "get_valuation_ratios",
            "get_consensus_estimates",
            "get_peer_comparison",
            "get_shareholder_structure",
        }
    ),
    "news": frozenset(
        {
            "search_news",
            "read_rss_item",
            "read_text_document",
            "get_announcements",
            "get_research_report",
        }
    ),
}

_ROLE_EXPLICIT_TOOLS: dict[str, frozenset[str]] = {
    "market": frozenset(
        {
            "read_realtime_quote",
            "read_recent_kline",
            "read_kline_range",
            "calculate_technical_indicator",
        }
    ),
    "fundamental": frozenset(),
    "news": frozenset(
        {
            "read_rss_source",
            "list_rss_source_catalog",
            "search_web_source",
            "read_web_source",
            "select_content_sources",
        }
    ),
}

_ROLE_FALLBACK_CATEGORIES: dict[str, frozenset[str]] = {
    "market": frozenset({"market", "macro", "deterministic_calculation", "source_read"}),
    "fundamental": frozenset({"financials", "analysis", "data"}),
    "news": frozenset(
        {
            "news_source",
            "research",
            "sentiment",
            "events",
            "source_search",
            "source_read",
            "source_catalog",
            "evidence_access",
        }
    ),
}


@dataclass(frozen=True)
class ExpertDefinition:
    """Server-owned definition of an expert that *may* join a Team run.

    Registration is deliberately separate from activation.  The supervisor
    can only select an id from this registry; it cannot create a new identity,
    widen a tool scope, or make every registered expert run automatically.
    ``graph_factory`` is an optional runtime hook for bespoke experts.  The
    current application reuses the existing bounded Agent subgraph when the
    hook is absent, while keeping the expert node, namespace and tool scope
    independent.
    """

    agent_id: str
    display_name: str
    capabilities: tuple[str, ...] = ()
    tool_scope: tuple[str, ...] | Callable[[Any], Iterable[str]] = ()
    input_schema: Any | None = None
    output_schema: Any | None = None
    graph_factory: Callable[..., Any] | None = None
    timeout_policy: dict[str, int] | None = None
    retry_policy: dict[str, int] | None = None
    max_concurrency: int = 1
    # Invocation budget, independent of how many distinct tools are allowed.
    # One indicator/source tool can be used for multiple required observations.
    # None inherits the run's shared budget. An explicit override is a
    # server-owned expert policy, never an implicit per-mode cutoff.
    max_tool_calls: int | None = None
    activation_policy: Callable[[str], bool] | None = None
    graph_node: str | None = None

    def __post_init__(self) -> None:
        normalized_id = str(self.agent_id or "").strip().lower()
        if not normalized_id or not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,95}", normalized_id):
            raise ValueError(f"invalid expert agent_id: {self.agent_id!r}")
        if not str(self.display_name or "").strip():
            raise ValueError(f"expert {normalized_id} must have a display_name")
        if int(self.max_concurrency) < 1 or int(self.max_concurrency) > 32:
            raise ValueError(f"expert {normalized_id} max_concurrency must be between 1 and 32")
        if self.max_tool_calls is not None and int(self.max_tool_calls) < 1:
            raise ValueError(f"expert {normalized_id} max_tool_calls must be positive")
        object.__setattr__(self, "agent_id", normalized_id)
        object.__setattr__(self, "display_name", str(self.display_name).strip()[:96])
        object.__setattr__(self, "capabilities", tuple(str(item).strip()[:96] for item in self.capabilities if str(item).strip()))
        if self.graph_node is not None:
            object.__setattr__(self, "graph_node", str(self.graph_node).strip()[:96] or None)

    def resolve_tool_names(self, registry: Any) -> list[str]:
        """Resolve and filter the server-owned read-only tool scope."""
        if callable(self.tool_scope):
            candidates = self.tool_scope(registry)
        else:
            candidates = self.tool_scope
        available = set(str(name) for name in registry.get_tool_names())
        names: list[str] = []
        for raw_name in candidates or ():
            name = str(raw_name).strip()
            if not name or name not in available or name in names:
                continue
            spec = registry.get_tool(name)
            if (
                spec is not None
                and str(getattr(spec, "effect", "read")) == "read"
                and getattr(spec, "effect_resolver", None) is None
            ):
                names.append(name)
        return names

    def fallback_enabled(self, user_text: str) -> bool:
        """Whether the server-owned emergency plan may select this expert.

        A registered expert is only available to the supervisor by default;
        registration must not silently turn a newly added expert into a
        production execution branch.  Built-ins explicitly opt into the
        deterministic fallback below, while extensions must provide an
        activation policy if they want the same safety-net behaviour.
        """
        if self.activation_policy is None:
            return False
        try:
            return bool(self.activation_policy(user_text))
        except Exception:
            return False

    @property
    def node_name(self) -> str:
        return self.graph_node or f"expert_{re.sub(r'[^a-zA-Z0-9_]+', '_', self.agent_id)}"


class ExpertRegistry:
    """Mutable-at-startup registry for dynamic Team experts."""

    def __init__(self, definitions: Iterable[ExpertDefinition] = ()) -> None:
        self._definitions: dict[str, ExpertDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ExpertDefinition) -> ExpertDefinition:
        if definition.agent_id in self._definitions:
            raise ValueError(f"duplicate expert agent_id: {definition.agent_id}")
        self._definitions[definition.agent_id] = definition
        return definition

    def get(self, agent_id: str) -> ExpertDefinition | None:
        return self._definitions.get(str(agent_id or "").strip().lower())

    def require(self, agent_id: str) -> ExpertDefinition:
        definition = self.get(agent_id)
        if definition is None:
            raise KeyError(f"unregistered Team expert: {agent_id}")
        return definition

    def all(self) -> tuple[ExpertDefinition, ...]:
        return tuple(self._definitions.values())

    def ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def capabilities(self, registry: Any) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": definition.agent_id,
                "display_name": definition.display_name,
                "capabilities": list(definition.capabilities),
                "tools": definition.resolve_tool_names(registry),
                "max_concurrency": definition.max_concurrency,
            }
            for definition in self.all()
        ]

    @classmethod
    def default(cls) -> "ExpertRegistry":
        """Create the built-in registry without making registration activation."""
        return cls(
            (
                ExpertDefinition(
                    agent_id="market",
                    display_name="行情分析",
                    capabilities=("行情", "技术面", "市场状态", "资金流"),
                    tool_scope=lambda registry: expert_tool_names(registry, "market"),
                    graph_node="MarketAgent",
                    activation_policy=lambda _question: True,
                ),
                ExpertDefinition(
                    agent_id="fundamental",
                    display_name="基本面分析",
                    capabilities=("财务", "估值", "经营质量", "分红"),
                    tool_scope=lambda registry: expert_tool_names(registry, "fundamental"),
                    graph_node="FundamentalAgent",
                    activation_policy=lambda _question: True,
                ),
                ExpertDefinition(
                    agent_id="news",
                    display_name="新闻分析",
                    capabilities=("新闻", "公告", "事件", "研究来源"),
                    tool_scope=lambda registry: expert_tool_names(registry, "news"),
                    graph_node="NewsResearchAgent",
                    activation_policy=lambda _question: True,
                ),
            )
        )


class ScopedToolRegistry:
    """Read-only view of a parent registry for one worker task.

    The view is deliberately a deny-by-default proxy.  A model-generated tool
    name outside the supervisor-approved set is invisible to both the bound
    schema and the execution middleware.
    """

    def __init__(self, registry: Any, allowed_names: Iterable[str]) -> None:
        self.parent = registry
        available = set(str(name) for name in registry.get_tool_names())
        self.allowed_names = tuple(
            name
            for name in dict.fromkeys(str(value).strip() for value in allowed_names)
            if name in available and self._is_read_tool(name)
        )

    def _is_read_tool(self, name: str) -> bool:
        spec = self.parent.get_tool(name)
        # Argument-dependent effects are not safe to expose to a worker: the
        # supervisor plan is read-only and cannot carry an approval interrupt.
        return (
            spec is not None
            and str(getattr(spec, "effect", "read")) == "read"
            and getattr(spec, "effect_resolver", None) is None
        )

    def get_tool_names(self) -> list[str]:
        return list(self.allowed_names)

    def get_all_schemas(
        self,
        *,
        include_server_controlled: bool = False,
    ) -> list[dict[str, Any]]:
        """Expose only the worker-approved schemas to its ToolCatalog."""
        allowed = set(self.allowed_names)
        return [
            schema
            for schema in self.parent.get_all_schemas(
                include_server_controlled=include_server_controlled
            )
            if str((schema.get("function") or {}).get("name") or "") in allowed
        ]

    def get_tool(self, name: str) -> Any | None:
        normalized = str(name or "").strip()
        if normalized not in self.allowed_names:
            return None
        return self.parent.get_tool(normalized)

    def get_tool_owner_module(self, name: str) -> str | None:
        if self.get_tool(name) is None:
            return None
        return self.parent.get_tool_owner_module(name)

    def supports_isolated_execution(self, name: str) -> bool:
        return self.get_tool(name) is not None and bool(self.parent.supports_isolated_execution(name))

    def get_module_names(self) -> list[str]:
        return list(self.parent.get_module_names())

    def normalize_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.get_tool(name)
        if tool is None:
            raise KeyError(f"Tool not found in worker scope: {name}")
        return self.parent.normalize_arguments(name, arguments)

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.get_tool(name) is None:
            raise KeyError(f"Tool not found in worker scope: {name}")
        return self.parent.validate_arguments(name, arguments)

    def validate_model_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
        validation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.get_tool(name) is None:
            raise KeyError(f"Tool not found in worker scope: {name}")
        return self.parent.validate_model_arguments(
            name, arguments, approved=approved, validation_context=validation_context,
        )

    def effect_for(self, name: str, arguments: dict[str, Any]) -> str:
        if self.get_tool(name) is None:
            raise KeyError(f"Tool not found in worker scope: {name}")
        return self.parent.effect_for(name, arguments)

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.get_tool(name) is None:
            raise KeyError(f"Tool not found in worker scope: {name}")
        return self.parent.execute(name, arguments)


def expert_tool_names(registry: Any, expert_id: str) -> list[str]:
    """Return the server-owned read capability set for one registered expert."""
    normalized_expert_id = str(expert_id or "").strip().lower()
    owner_modules = _ROLE_OWNER_MODULES.get(normalized_expert_id, frozenset())
    explicit = _ROLE_EXPLICIT_TOOLS.get(normalized_expert_id, frozenset())
    fallback_categories = _ROLE_FALLBACK_CATEGORIES.get(normalized_expert_id, frozenset())
    names: list[str] = []
    for raw_name in registry.get_tool_names():
        name = str(raw_name)
        spec = registry.get_tool(name)
        if (
            spec is None
            or str(getattr(spec, "effect", "read")) != "read"
            or getattr(spec, "effect_resolver", None) is not None
        ):
            continue
        owner = str(registry.get_tool_owner_module(name) or "")
        category = str(getattr(spec, "category", "") or "")
        if (
            name in _COMMON_READ_TOOLS
            or name in explicit
            or owner in owner_modules
            or (owner == "injected" and category in fallback_categories)
        ):
            names.append(name)
    return names


class TaskScopedExecutor:
    """Prefix action identities before delegating to the shared executor."""

    def __init__(
        self,
        executor: Any,
        *,
        task_id: str,
        agent_id: str,
        expert_id: str,
        display_name: str | None = None,
        events: Any | None = None,
        controller: Any | None = None,
    ) -> None:
        self.parent = executor
        self.task_id = task_id
        self.agent_id = agent_id
        self.expert_id = str(expert_id).strip()
        self.display_name = str(display_name or "").strip()
        self.events = events
        self.controller = controller

    async def execute(
        self,
        action: dict[str, Any],
        *,
        approved: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        original_action_id = str(action.get("action_id") or "").strip()
        if not original_action_id:
            raise ValueError("worker tool call is missing an action id")
        scoped_action = dict(action)
        scoped_action["action_id"] = f"{self.agent_id}:{original_action_id}"[:192]
        scoped_action.update(
            {
                "task_id": self.task_id,
                "agent_id": self.agent_id,
                "expert_id": self.expert_id,
                "agent_display_name": self.display_name,
            }
        )
        with execution_event_scope(
            events=self.events or getattr(self.parent, "events", None),
            controller=self.controller or getattr(self.parent, "controller", None),
        ):
            # AtomicToolExecutor resolves the event/controller override from
            # the context variable.  Test executors simply ignore the scope.
            record, evidence = await self.parent.execute(scoped_action, approved=approved)
        projected_record = dict(record)
        projected_record.update(
            {
                "task_id": self.task_id,
                "agent_id": self.agent_id,
                "expert_id": self.expert_id,
                "worker_tool_call_id": original_action_id,
            }
        )
        projected_evidence = dict(evidence) if isinstance(evidence, dict) else None
        if projected_evidence is not None:
            projected_evidence.update(
                {
                    "task_id": self.task_id,
                    "agent_id": self.agent_id,
                    "expert_id": self.expert_id,
                    "agent_display_name": self.display_name,
                    "worker_tool_call_id": original_action_id,
                }
            )
        return projected_record, projected_evidence


__all__ = [
    "ExpertDefinition",
    "ExpertRegistry",
    "ScopedToolRegistry",
    "TaskScopedExecutor",
    "expert_tool_names",
]
