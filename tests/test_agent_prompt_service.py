from src.agent.langgraph_runtime.prompts import DEFAULT_AGENT_SYSTEM_PROMPT
from src.services.agent_prompt_service import AgentPromptService


def test_prompt_fallback_is_stable_and_not_bound_to_http_chat_module() -> None:
    assert AgentPromptService._fallback_prompt() == DEFAULT_AGENT_SYSTEM_PROMPT
    assert "通用" in DEFAULT_AGENT_SYSTEM_PROMPT
