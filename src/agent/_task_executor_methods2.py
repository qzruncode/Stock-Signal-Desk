"""WorkflowExecutor method group 2."""

from __future__ import annotations

from src.agent.task_executor import (
    asyncio,
    hashlib,
    json,
    logging,
    dataclass,
    field,
    replace,
    Any,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    ConfirmationState,
    EffectClass,
    ResolvedTask,
    SecurityEntity,
    TaskResource,
    WorkflowCall,
    WorkflowCompileError,
    compile_task,
    project_task_collection,
    workflow_for,
    ToolRegistry,
    logger,
    PolicyViolation,
    ConfirmationRequired,
    WorkflowUnavailable,
    ValidatedCall,
    CallOutcome,
    TaskExecutionResult,
    PlanExecutionResult,
    CallRunner,
    ResultProcessorRunner,
    OutcomeObserver,
    action_fingerprint,
    WorkflowPolicyValidator,
 )

class _WorkflowExecutorMethods2:
    async def _execute_or_reuse(
        self,
        task: ResolvedTask,
        item: ValidatedCall,
    ) -> CallOutcome:
        signature = self._signature(item.call.tool_name, item.arguments)
        owner = False
        async with self._cache_lock:
            future = self._call_futures.get(signature)
            if future is None:
                if self._execution_count >= self.max_plan_tool_calls:
                    return CallOutcome(
                        task_id=task.task_id,
                        step_id=item.call.step_id,
                        tool_name=item.call.tool_name,
                        arguments=item.arguments,
                        result={
                            "success": False,
                            "errors": ["整轮标准任务调用预算已用完。"],
                            "partial": False,
                        },
                        executed=False,
                        reused=False,
                    )
                future = asyncio.get_running_loop().create_future()
                self._call_futures[signature] = future
                self._execution_count += 1
                owner = True

        if owner:
            try:
                result = await self.runner(item.call, item.arguments)
                if not isinstance(result, dict):
                    result = {
                        "success": False,
                        "errors": ["工具返回值不是对象。"],
                        "partial": False,
                    }
            except Exception as exc:
                result = {
                    "success": False,
                    "errors": [f"工具执行失败：{exc}"],
                    "partial": False,
                }
            if not future.done():
                future.set_result(result)
        result = await future
        return CallOutcome(
            task_id=task.task_id,
            step_id=item.call.step_id,
            tool_name=item.call.tool_name,
            arguments=item.arguments,
            result=result,
            executed=owner,
            reused=not owner,
        )
    @staticmethod
    def _signature(tool_name: str, arguments: Mapping[str, Any]) -> str:
        payload = json.dumps(
            {"tool": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
