# -*- coding: utf-8 -*-
"""Deterministic release manifest for the built-in capability registry."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.agent.orchestrator_v2.contracts import Capability
from src.agent.orchestrator_v2.registry import capability_for
from src.agent.task_workflows import StandardTaskKind, workflow_for


CAPABILITY_MANIFEST_VERSION = "capability-manifest-1"


def build_capability_release_manifest() -> dict[str, Any]:
    capabilities: list[dict[str, Any]] = []
    for capability in sorted(Capability, key=lambda item: item.value):
        spec = capability_for(capability)
        workflow = workflow_for(StandardTaskKind(capability.value))
        capabilities.append(
            {
                "capability": capability.value,
                "version": spec.version,
                "intent_schema_version": spec.schema_version,
                "effect": workflow.effect.value,
                "confirmation_policy": (
                    workflow.confirmation_policy.value
                ),
                "enabled": workflow.enabled,
                "tools": sorted(workflow.tool_whitelist),
                "result_contract": workflow.result_contract,
                "renderer": spec.renderer.value,
                "input_resources": sorted(
                    item.value for item in spec.input_resources
                ),
                "output_resources": sorted(
                    item.value for item in spec.output_resources
                ),
            }
        )
    manifest = {
        "manifest_version": CAPABILITY_MANIFEST_VERSION,
        "capabilities": capabilities,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**manifest, "registry_fingerprint": fingerprint}


__all__ = [
    "CAPABILITY_MANIFEST_VERSION",
    "build_capability_release_manifest",
]
