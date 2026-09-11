# -*- coding: utf-8 -*-
"""SQLAlchemy ORM model definitions for the A-share stock analysis system."""

import json
import logging
from datetime import datetime, date
from typing import Any, Dict, Optional

from sqlalchemy import (
    Column,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    Integer,
    ForeignKey,
    Index,
    UniqueConstraint,
    Text,
)
from sqlalchemy.orm import declarative_base

logger = logging.getLogger(__name__)

Base = declarative_base()



from . import _models_group1 as _models_group1
for _name in _models_group1.__all__:
    globals()[_name] = getattr(_models_group1, _name)

from . import _models_group2 as _models_group2
for _name in _models_group2.__all__:
    globals()[_name] = getattr(_models_group2, _name)

from . import _models_group3 as _models_group3
for _name in _models_group3.__all__:
    globals()[_name] = getattr(_models_group3, _name)

from . import _models_agent_quality as _models_agent_quality
for _name in _models_agent_quality.__all__:
    globals()[_name] = getattr(_models_agent_quality, _name)

from . import _models_financial_lifecycle as _models_financial_lifecycle
for _name in _models_financial_lifecycle.__all__:
    globals()[_name] = getattr(_models_financial_lifecycle, _name)

from . import _models_agent_governance as _models_agent_governance
for _name in _models_agent_governance.__all__:
    globals()[_name] = getattr(_models_agent_governance, _name)
