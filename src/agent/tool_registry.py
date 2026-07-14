# -*- coding: utf-8 -*-
"""Backward-compatible import for the tool registry.

The registry and every registered tool adapter are managed in ``src.tools``.
Keep this module so older imports continue to work while new code uses
``src.tools.registry`` directly.
"""

from src.tools.registry import ToolDef, ToolRegistry

__all__ = ["ToolDef", "ToolRegistry"]
