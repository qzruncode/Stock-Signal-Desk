# -*- coding: utf-8 -*-
"""Mixin imports — attach all mixin methods to DatabaseManager."""

from src.storage.mixins.news import NewsMixin
from src.storage.mixins.macro import MacroMixin
from src.storage.mixins.analysis import AnalysisMixin
from src.storage.mixins.chat import ChatMixin
from src.storage.mixins.batch import BatchMixin
from src.storage.mixins.watchlist import WatchlistMixin
from src.storage.mixins.agent_prompt import AgentPromptMixin
from src.storage.mixins.agent_artifact import AgentArtifactMixin
from src.storage.mixins.agent_run_trace import AgentRunTraceMixin
from src.storage.mixins.agent_runtime import AgentRuntimeMixin
from src.storage.mixins.agent_quality import AgentQualityMixin
from src.storage.mixins.financial_lifecycle import FinancialLifecycleMixin
from src.storage.mixins.text_document import TextDocumentMixin

__all__ = [
    "NewsMixin",
    "MacroMixin",
    "AnalysisMixin",
    "ChatMixin",
    "BatchMixin",
    "WatchlistMixin",
    "AgentPromptMixin",
    "AgentArtifactMixin",
    "AgentRunTraceMixin",
    "AgentRuntimeMixin",
    "AgentQualityMixin",
    "FinancialLifecycleMixin",
    "TextDocumentMixin",
]
