"""Runtime data initialization for the capability registry (part 3)."""

SOURCE = (
    'CAPABILITY_REGISTRY: Mapping[\n'
    '    Capability,\n'
    '    CapabilitySpec[Any, TaskOutcomeV2],\n'
    '] = MappingProxyType({capability: _make_spec(capability) for capability in Capability})\n'
    '__all__ = [\n'
    '    "CAPABILITY_REGISTRY",\n'
    '    "capability_catalog",\n'
    '    "capability_for",\n'
    '    "migration_coverage",\n'
    '    "normalize_capability_intent",\n'
    ']\n'
)


def initialize(namespace: dict[str, object]) -> None:
    exec(SOURCE, namespace)
