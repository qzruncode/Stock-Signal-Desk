"""WorkflowExecutor method group 1."""

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

class _WorkflowExecutorMethods1:
    def __init__(
        self,
        registry: ToolRegistry,
        runner: CallRunner,
        *,
        max_plan_tool_calls: int = 6200,
        approved_actions: Iterable[str] = (),
        processor_runner: ResultProcessorRunner | None = None,
        outcome_observer: OutcomeObserver | None = None,
        execution_policies: Mapping[str, Any] | None = None,
        capability_authorizations: Mapping[str, bool] | None = None,
    ) -> None:
        self.registry = registry
        self.runner = runner
        self.validator = WorkflowPolicyValidator(
            registry,
            approved_actions=approved_actions,
            execution_policies=execution_policies,
            capability_authorizations=capability_authorizations,
        )
        self.max_plan_tool_calls = max_plan_tool_calls
        self.processor_runner = processor_runner
        self.outcome_observer = outcome_observer
        self.execution_policies = dict(execution_policies or {})
        self._cache_lock = asyncio.Lock()
        self._call_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._execution_count = 0
    async def execute(self, tasks: list[ResolvedTask]) -> PlanExecutionResult:
        """Run independent read tasks concurrently and dependencies in order."""
        task_by_id = {task.task_id: task for task in tasks}
        pending = set(task_by_id)
        results: dict[str, TaskExecutionResult] = {}

        while pending:
            blocked = [
                task_id
                for task_id in pending
                if any(
                    dependency in results and not results[dependency].success
                    for dependency in task_by_id[task_id].candidate.depends_on
                )
            ]
            for task_id in blocked:
                pending.remove(task_id)
                results[task_id] = TaskExecutionResult(
                    task=task_by_id[task_id],
                    status="skipped",
                    errors=["前置标准任务未成功，程序已阻止后续执行。"],
                )

            ready = [
                self._bind_dependency_outputs(task_by_id[task_id], results)
                for task_id in pending
                if set(task_by_id[task_id].candidate.depends_on) <= set(results)
            ]
            if not ready:
                if pending:
                    raise PolicyViolation("task dependency graph made no progress")
                break

            reads = [task for task in ready if workflow_for(task.kind).effect == EffectClass.READ]
            effects = [task for task in ready if workflow_for(task.kind).effect != EffectClass.READ]

            if reads:
                read_results = await asyncio.gather(*(self._execute_task(task) for task in reads))
                for result in read_results:
                    results[result.task.task_id] = result
                    pending.remove(result.task.task_id)

            # Side effects are intentionally serialized even when their task
            # nodes are independent.  This keeps confirmation and mutation
            # boundaries visible and avoids racing stateful operations.
            for task in effects:
                result = await self._execute_task(task)
                results[result.task.task_id] = result
                pending.remove(result.task.task_id)

        ordered = [results[task.task_id] for task in tasks]
        return PlanExecutionResult(tasks=ordered)
    def _bind_dependency_outputs(
        self,
        task: ResolvedTask,
        results: Mapping[str, TaskExecutionResult],
    ) -> ResolvedTask:
        spec = workflow_for(task.kind)
        bound = task
        parameters = dict(task.parameters)
        parameters_changed = False
        for parameter, resource in spec.input_resource_parameters.items():
            value = _resource_from_dependencies(
                task.candidate.depends_on,
                results,
                resource,
            )
            if value is None:
                continue
            # An explicit typed resource edge is authoritative. Semantic
            # parameters and incidental current-message entities describe the
            # request, but must never replace or narrow the producer's frozen
            # collection.
            parameters[parameter] = value
            parameters_changed = True
        if parameters_changed:
            bound = replace(
                bound,
                candidate=bound.candidate.model_copy(update={"execution_parameters": parameters}),
            )

        if TaskResource.SECURITY_COLLECTION not in spec.input_resources:
            return bound
        security_resource = _resource_from_dependencies(
            bound.candidate.depends_on,
            results,
            TaskResource.SECURITY_COLLECTION,
        )
        if security_resource is None:
            return bound
        entities = _security_entities_from_resource(security_resource)
        return replace(
            bound,
            symbols=tuple(entity.symbol for entity in entities),
            entity_names=tuple((entity.symbol, entity.name) for entity in entities),
        )
    async def _execute_task(self, task: ResolvedTask) -> TaskExecutionResult:
        try:
            validated = self.validator.preflight_task(task)
        except ConfirmationRequired as exc:
            return TaskExecutionResult(
                task=task,
                status="blocked",
                errors=[str(exc)],
                blocked_reason="confirmation_required",
            )
        except (PolicyViolation, WorkflowCompileError, ValueError) as exc:
            return TaskExecutionResult(
                task=task,
                status="blocked",
                errors=[str(exc)],
                blocked_reason="policy_violation",
            )
        spec = workflow_for(task.kind)
        if not validated and not spec.result_processor:
            return TaskExecutionResult(
                task=task,
                status="completed",
                output_entities=project_task_collection(task, ()),
            )

        pending = {item.call.step_id: item for item in validated}
        completed: dict[str, CallOutcome] = {}
        outcomes: list[CallOutcome] = []
        errors: list[str] = []
        dependency_blocked_steps = 0
        while pending:
            skipped = [
                step_id
                for step_id, item in pending.items()
                if any(
                    predecessor in completed and not completed[predecessor].success
                    for predecessor in item.call.depends_on_steps
                )
            ]
            for step_id in skipped:
                item = pending.pop(step_id)
                outcome = CallOutcome(
                    task_id=task.task_id,
                    step_id=step_id,
                    tool_name=item.call.tool_name,
                    arguments=item.arguments,
                    result={
                        "success": False,
                        "errors": ["前置步骤失败，程序已阻止调用。"],
                        "partial": False,
                    },
                    executed=False,
                    reused=False,
                )
                completed[step_id] = outcome
                dependency_blocked_steps += 1

            ready = [
                item
                for item in pending.values()
                if (set(item.call.depends_on_steps) | set(item.call.after_steps)) <= set(completed)
            ]
            if not ready:
                if pending:
                    errors.append("固定 Workflow 的步骤依赖无法继续。")
                break
            execution_policy = self.execution_policies.get(task.task_id)
            step_limit = (
                int(execution_policy.max_parallelism) if execution_policy is not None else spec.max_parallel_steps
            )
            semaphore = asyncio.Semaphore(step_limit)

            async def execute_ready(item: ValidatedCall) -> CallOutcome:
                async with semaphore:
                    try:
                        runtime_arguments = dict(item.arguments)
                        for parameter, source_step in item.call.result_bindings:
                            source = completed.get(source_step)
                            if source is None or not source.success:
                                raise PolicyViolation(f"{item.call.step_id} cannot bind failed " f"step {source_step}")
                            runtime_arguments[parameter] = self.registry.project_bound_argument(
                                item.call.tool_name,
                                parameter,
                                source.result,
                            )
                        runtime_item = ValidatedCall(
                            call=item.call,
                            arguments=self.registry.validate_arguments(
                                item.call.tool_name,
                                runtime_arguments,
                            ),
                        )
                    except Exception as exc:
                        return CallOutcome(
                            task_id=task.task_id,
                            step_id=item.call.step_id,
                            tool_name=item.call.tool_name,
                            arguments=dict(item.arguments),
                            result={
                                "success": False,
                                "errors": ["前置步骤结果绑定失败：" f"{type(exc).__name__}: {exc}"],
                                "partial": False,
                            },
                            executed=False,
                            reused=False,
                        )
                    guard = item.call.execution_guard
                    if guard is not None:
                        try:
                            source = completed.get(guard.source_step)
                            if source is None or not source.success:
                                raise PolicyViolation(
                                    f"{item.call.step_id} cannot evaluate guard "
                                    f"from failed step {guard.source_step}"
                                )
                            guard_value = _result_path_value(
                                source.result,
                                guard.result_path,
                            )
                            guard_allows_execution = any(guard_value == allowed for allowed in guard.allowed_values)
                            if not guard_allows_execution:
                                projected_result = await asyncio.to_thread(
                                    self.registry.project_guard_blocked_result,
                                    item.call.tool_name,
                                    runtime_item.arguments,
                                )
                                return CallOutcome(
                                    task_id=task.task_id,
                                    step_id=item.call.step_id,
                                    tool_name=item.call.tool_name,
                                    arguments=runtime_item.arguments,
                                    result=projected_result,
                                    executed=False,
                                    reused=False,
                                )
                        except Exception as exc:
                            return CallOutcome(
                                task_id=task.task_id,
                                step_id=item.call.step_id,
                                tool_name=item.call.tool_name,
                                arguments=runtime_item.arguments,
                                result={
                                    "success": False,
                                    "errors": ["执行守卫未能形成确定性终态：" f"{type(exc).__name__}: {exc}"],
                                    "partial": False,
                                },
                                executed=False,
                                reused=False,
                            )
                    return await self._execute_or_reuse(task, runtime_item)

            active = [asyncio.create_task(execute_ready(item)) for item in ready]
            try:
                for future in asyncio.as_completed(active):
                    outcome = await future
                    pending.pop(outcome.step_id, None)
                    completed[outcome.step_id] = outcome
                    outcomes.append(outcome)
                    if self.outcome_observer is not None:
                        try:
                            await self.outcome_observer(
                                task,
                                outcome,
                                len(completed),
                                len(validated),
                            )
                        except Exception as exc:
                            logger.warning(
                                "Workflow outcome observer failed for task=%s step=%s: %s",
                                task.task_id,
                                outcome.step_id,
                                exc,
                            )
            finally:
                unfinished = [future for future in active if not future.done()]
                for future in unfinished:
                    future.cancel()
                if unfinished:
                    await asyncio.gather(*unfinished, return_exceptions=True)

        if dependency_blocked_steps:
            errors.append(f"{dependency_blocked_steps} 个后续步骤因前置步骤失败未执行。")

        outcome_order = {item.call.step_id: index for index, item in enumerate(validated)}
        outcomes.sort(key=lambda item: outcome_order.get(item.step_id, len(outcome_order)))

        successful_outcomes = [item for item in outcomes if item.success]
        failed_outcomes = [item for item in outcomes if not item.success]
        partial_sources_accepted = (
            spec.allow_partial_tool_failures and bool(successful_outcomes) and bool(failed_outcomes)
        )
        status = "completed" if (not errors and (not failed_outcomes or partial_sources_accepted)) else "failed"
        derived_results: list[dict[str, Any]] = []
        resource_outputs: dict[str, Any] = {}
        if status == "completed" and spec.result_processor:
            if self.processor_runner is None:
                status = "failed"
                errors.append(f"标准任务缺少已声明的结果处理器：{spec.result_processor}")
            else:
                try:
                    processor_result = await self.processor_runner(
                        spec.result_processor,
                        task,
                        [outcome.evidence() for outcome in outcomes],
                    )
                    packet = {
                        "task_id": task.task_id,
                        "step_id": f"result_{spec.result_processor}",
                        "processor": spec.result_processor,
                        "result": processor_result,
                    }
                    derived_results.append(packet)
                    if processor_result.get("success") is False:
                        status = "failed"
                        errors.extend(
                            str(value)
                            for value in processor_result.get("errors") or [f"{spec.result_processor} failed"]
                        )
                    raw_resources = processor_result.get("resource_outputs")
                    if isinstance(raw_resources, Mapping):
                        resource_outputs.update({str(key): value for key, value in raw_resources.items()})
                except Exception as exc:
                    status = "failed"
                    errors.append(f"标准任务结果处理失败：{type(exc).__name__}: {exc}")

        result_context = [
            *(outcome.evidence() for outcome in outcomes),
            *derived_results,
        ]
        authoritative_security = resource_outputs.get(TaskResource.SECURITY_COLLECTION.value)
        output_entities = (
            (
                _security_entities_from_resource(authoritative_security)
                if isinstance(authoritative_security, list)
                else project_task_collection(task, result_context)
            )
            if status == "completed"
            else ()
        )
        max_output_entities = spec.max_output_entities
        if status == "completed" and max_output_entities is not None and len(output_entities) > max_output_entities:
            status = "failed"
            errors.append(
                f"标准任务产出 {len(output_entities)} 个证券对象，超过资源契约容量 "
                f"{max_output_entities}；未截断集合。"
            )
            output_entities = ()
        if status == "completed":
            for parameter, resource in spec.parameter_output_resources.items():
                value = task.parameters.get(parameter)
                if value not in (None, [], {}):
                    resource_outputs[resource.value] = value
            if TaskResource.SECURITY_COLLECTION in spec.output_resources:
                resource_outputs[TaskResource.SECURITY_COLLECTION.value] = [
                    {"symbol": entity.symbol, "name": entity.name} for entity in output_entities
                ]
            missing_resources = [
                resource.value for resource in spec.output_resources if resource.value not in resource_outputs
            ]
            if missing_resources:
                status = "failed"
                errors.append("标准任务没有发布声明的资源：" + "、".join(missing_resources))
                output_entities = ()
                resource_outputs = {}
        return TaskExecutionResult(
            task=task,
            status=status,
            calls=outcomes,
            derived_results=derived_results,
            errors=errors,
            output_entities=output_entities,
            resource_outputs=resource_outputs,
        )
