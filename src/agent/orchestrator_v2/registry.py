# -*- coding: utf-8 -*-
"""Single-source capability contracts for the unified Agent control plane."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, cast

from pydantic import BaseModel

from src.agent.orchestrator_v2 import intents as intent_models
from src.agent.orchestrator_v2.contracts import (
    AgentErrorCode,
    AssumptionRecord,
    CacheReuseScope,
    Capability,
    CapabilitySpec,
    CoverageV2,
    EffectLevel,
    ExecutionPolicy,
    EvidenceDimension,
    FreshnessPolicy,
    InputReferenceV2,
    NormalizedIntent,
    OrchestratorV2Error,
    ProjectedResourceV2,
    QuestionType,
    RendererMode,
    ResourceType,
    ResultSelectionV2,
    TaskOutcomeV2,
)
from src.agent.orchestrator_v2.intents import (
    CollectionFinancialFilterIntent,
    FiscalYearPeriod,
    LatestReportPeriod,
    MoneyAmount,
    OutputRequestV2,
    ThemeStockDiscoveryIntent,
    TtmPeriod,
)
from src.agent.task_workflows import (
    CollectionBehavior,
    EffectClass,
    StandardTaskKind,
    TaskResource,
    workflow_for,
)
from src.services.buy_criteria.mainline_policy import (
    MainlineStrategyProfile,
    normalize_mainline_strategy,
)


_OUTPUT_DEFAULTS: Mapping[str, Any] = MappingProxyType(
    {
        "language": "zh-CN",
        "format": "concise",
        "include_assumptions": True,
    }
)



from . import _registry_functions1 as _registry_functions1
from . import _registry_functions2 as _registry_functions2


def _bind_extracted_function(_member):
    import functools
    import types

    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _function_module in (_registry_functions1, _registry_functions2):
    for _function_name in _function_module.__all__:
        globals()[_function_name] = _bind_extracted_function(getattr(_function_module, _function_name))

from . import _registry_data1 as _registry_data1
from . import _registry_data2 as _registry_data2
from . import _registry_data3 as _registry_data3
_registry_data1.initialize(globals())
_registry_data2.initialize(globals())
_registry_data3.initialize(globals())
