"""Versioned local experiments over the real application graph and frozen tools.

LangGraph executes, the existing quality contract scores evidence, and AgentEvals
compares trajectories. No production database or external tools are involved.
Live model evaluation is an explicit caller choice, never the default.
"""

from copy import deepcopy
import hashlib
import json
from time import perf_counter

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from src.agent.evaluation import score_agent_run_snapshot
from src.agent.langgraph_runtime.runtime import LangGraphRuntimeManager
from src.tools.base import ToolSpec
from src.tools.registry import ToolRegistry


class FixtureChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)


def frozen_registry(definitions):
    def reader(observations):
        def read(**arguments):
            for item in observations:
                if item["arguments"] == arguments:
                    return deepcopy(item["result"])
            raise ValueError("No frozen observation matches the requested arguments")
        return read
    return ToolRegistry.from_tools([
        ToolSpec(name=item["name"], description=item["description"], parameters=item["parameters"],
                 executor=reader(item["observations"]), effect="read", max_attempts=1)
        for item in definitions
    ])


def tool_trajectory(calls):
    return [AIMessage(content="", tool_calls=[{
        "name": call["name"], "args": call["args"], "id": f"call-{index}", "type": "tool_call",
    } for index, call in enumerate(calls)])]


async def evaluate_case(case, *, system_prompt="", live_model_config=None):
    from agentevals.trajectory.match import create_trajectory_match_evaluator

    manager = LangGraphRuntimeManager(registry=frozen_registry(case.get("tools", [])))
    await manager.start(testing=True)
    started = perf_counter()
    try:
        model = None if live_model_config is not None else FixtureChatModel(
            messages=iter([AIMessage(**item) for item in case["model_responses"]]),
            disable_streaming=True,
        )
        result = await manager.run_new(
            messages=[{"role": "user", "content": case["question"]}], user_text=case["question"],
            system_prompt=system_prompt, llm_config=live_model_config or {}, database=None, controller=None,
            run_id=f"evaluation-{case['id']}", conversation_id=f"evaluation-{case['id']}",
            run_attempt=1, tenant_id="evaluation", owner_id="evaluation", model=model,
        )
        state = result.state
        projection = {key: state.get(key, []) for key in ("tool_results", "evidence", "claim_evidence")}
        score = score_agent_run_snapshot({
            "run": {"status": result.status, "final_text": result.final_text, "tool_call_count": state.get("tool_call_count", 0)},
            "quality_projection": {"engine": "langgraph_agent_loop", **projection},
        }, case.get("expectations", {}))
        calls = [{"name": call["tool_name"], "args": call.get("arguments", {})} for call in state.get("tool_results", [])]
        evaluator = create_trajectory_match_evaluator(trajectory_match_mode=case.get("trajectory_match_mode", "unordered"))
        trajectory = evaluator(outputs=tool_trajectory(calls), reference_outputs=tool_trajectory(case.get("reference_tools", [])))
        passed = (result.status == case.get("expected_status", "completed")
                  and score["passed"] == case.get("expected_quality_pass", True) and bool(trajectory["score"]))
        return {"id": case["id"], "passed": passed, "status": result.status, "quality": score,
                "trajectory": trajectory, "duration_seconds": round(perf_counter() - started, 3), "answer": result.final_text}
    finally:
        await manager.close()


async def evaluate_suite(dataset, *, system_prompt="", live_model_config=None):
    from langsmith import tracing_context

    if dataset.get("version") != 1 or not dataset.get("cases"):
        raise ValueError("Expected a nonempty version-1 regression dataset")
    ids = [case["id"] for case in dataset["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Dataset case IDs must be unique")
    results = []
    # Even if a developer shell enables tracing, local regression stays local.
    with tracing_context(enabled=False):
        for case in dataset["cases"]:
            try:
                results.append(await evaluate_case(case, system_prompt=system_prompt, live_model_config=live_model_config))
            except Exception as exc:
                # Provider exceptions may contain request URLs or credentials.
                results.append({"id": case["id"], "passed": False, "error_type": type(exc).__name__,
                                "error": "Evaluation failed; inspect the local fixture/model configuration."})
    return {"dataset_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest(),
            "prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
            "mode": "live_model_frozen_tools" if live_model_config is not None else "frozen_model_and_tools",
            "model": live_model_config.get("model") if live_model_config is not None else "fixture",
            "passed": all(item["passed"] for item in results), "results": results}


def compare_experiments(current, baseline):
    if current["dataset_sha256"] != baseline["dataset_sha256"]:
        raise ValueError("Cannot compare different datasets")
    previous = {item["id"]: item for item in baseline["results"]}
    if set(previous) != {item["id"] for item in current["results"]}:
        raise ValueError("Experiment case coverage differs")
    return [{"id": item["id"], "before": previous[item["id"]]["passed"], "after": item["passed"]}
            for item in current["results"] if item["passed"] != previous[item["id"]]["passed"]]
