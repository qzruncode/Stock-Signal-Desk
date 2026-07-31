# -*- coding: utf-8 -*-
"""Field metadata for the Notification category."""

from __future__ import annotations

from typing import Any, Dict, List

from src.notification_routing import ROUTABLE_NOTIFICATION_CHANNELS

NOTIFICATION_SEVERITIES = ("info", "warning", "error", "critical")



from ._notification_fields1 import _FIELD_DEFINITIONS as _notification_fields1
from ._notification_fields2 import _FIELD_DEFINITIONS as _notification_fields2
from ._notification_fields3 import _FIELD_DEFINITIONS as _notification_fields3

_FIELD_DEFINITIONS = {}
for _fields in (_notification_fields1, _notification_fields2, _notification_fields3):
    _FIELD_DEFINITIONS.update(_fields)
