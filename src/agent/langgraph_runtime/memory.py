"""Run-scoped binding of LangChain's maintained summarization middleware."""

import os
import logging
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.callbacks import BaseCallbackHandler


class SummaryFailureCallback(BaseCallbackHandler):
    """Observe the public model callback, without inspecting summary text."""

    failed = False

    def on_llm_error(self, error, **kwargs):
        self.failed = True

    def on_llm_end(self, response, **kwargs):
        if not response.generations or not response.generations[0]:
            self.failed = True
            return
        message = getattr(response.generations[0][0], "message", None)
        if message is None or not message.text.strip():
            self.failed = True


class ConversationMemoryMiddleware(AgentMiddleware):
    async def abefore_model(self, state, runtime):
        try:
            threshold = int(os.getenv("AGENT_SUMMARY_TRIGGER_TOKENS", "60000"))
        except ValueError:
            logging.getLogger(__name__).warning("Invalid AGENT_SUMMARY_TRIGGER_TOKENS; leaving messages intact")
            return None
        if threshold <= 0:
            return None
        # Evidence and claim ledgers remain separate checkpoint channels. Native
        # summarization preserves valid tool-call/result message boundaries.
        observer = SummaryFailureCallback()
        callbacks = runtime.context.model.callbacks
        if callbacks is None or isinstance(callbacks, list):
            callbacks = [*(callbacks or []), observer]
        else:
            callbacks = callbacks.copy()
            callbacks.add_handler(observer)
        middleware = SummarizationMiddleware(
            model=runtime.context.model.model_copy(update={"callbacks": callbacks}),
            trigger=("tokens", threshold), keep=("messages", 20),
        )
        update = await middleware.abefore_model(state, runtime)
        # Native summarization catches provider errors. Never apply its history
        # replacement when no successful summary was generated.
        return None if observer.failed else update
