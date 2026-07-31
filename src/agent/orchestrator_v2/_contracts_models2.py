"""Typed contract model group 2."""

from __future__ import annotations

import src.agent.orchestrator_v2.contracts as _contracts

for _name, _value in vars(_contracts).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

__all__ = ['NormalizedIntent', 'CapabilitySpec', 'CompiledCallV2']

@dataclass(frozen=True)
class NormalizedIntent(Generic[IntentT]):
    intent: IntentT
    execution_parameters: Mapping[str, Any]
    assumptions: tuple[AssumptionRecord, ...] = ()

@dataclass(frozen=True)
class CapabilitySpec(Generic[IntentT, ResultT]):
    capability: Capability
    version: str
    title: str
    description: str
    intent_model: type[IntentT]
    result_model: type[ResultT]
    input_resources: frozenset[ResourceType]
    output_resources: frozenset[ResourceType]
    compiler: Compiler[IntentT]
    execution_policy: ExecutionPolicy
    freshness_policy: FreshnessPolicy
    projector: Callable[
        [ResultT, ResourceType],
        ProjectedResourceV2 | None,
    ]
    renderer: RendererMode
    supported_question_types: frozenset[QuestionType]
    evidence_dimensions: frozenset[EvidenceDimension]
    supported_claims: tuple[str, ...]
    limitations: tuple[str, ...]
    fallback_capabilities: tuple[Capability, ...] = ()
    auto_expandable: bool = False
    allow_direct_entities: bool = False
    supports_result_selection: bool = False
    program_default_fields: frozenset[str] = field(default_factory=frozenset)
    subsumes_capabilities: frozenset[Capability] = field(default_factory=frozenset)

    @property
    def schema_version(self) -> str:
        schema = self.intent_model.model_json_schema()
        digest = sha256(json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return f"{self.version}:{digest}"

@dataclass(frozen=True)
class CompiledCallV2(Generic[ArgsT]):
    task_id: str
    step_id: str
    tool_name: str
    arguments: ArgsT
    depends_on_steps: tuple[str, ...]
    after_steps: tuple[str, ...]
    result_bindings: tuple[tuple[str, str], ...]
    idempotency_key: str
