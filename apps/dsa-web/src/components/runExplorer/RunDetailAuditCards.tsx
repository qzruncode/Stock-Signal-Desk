import { Activity, CheckCircle2, Database, LoaderCircle, ShieldCheck, Users } from "lucide-react";
import type { AgentGoalTrace, AgentPlanningTrace, AgentTeamTrace } from "../../api/agent";
import { AssistantMarkdown } from "../assistant-ui/AssistantMarkdownText";
import { Badge, Card } from "../common";
import { agentModeLabel } from "../../utils/agentMode";
import { arrayFrom, field, numberValue, record, stringList, text } from "./RunDetailUtils";
import { Metric } from "./RunDetailMetric";

/** Display the persisted model reports, not summaries inferred from tool counts. */
export function PlanningAuditCard({ planning }: { planning?: AgentPlanningTrace }) {
  if (!planning) return null;
  const plan = record(planning.plan);
  const steps = arrayFrom(plan.steps).map(record);
  const reports = planning.stepReports ?? [];
  const labels: Record<string, string> = {
    completed: '已完成核验', running: '执行中', pending: '尚未执行', blocked: '存在缺口',
    partial: '未完整完成', executing: '执行中', finalizing: '最终核验中', not_required: '直接执行',
  };
  const revisions = (planning.updates ?? []).filter((event) => (
    text(field(event.details, ['planningPhase', 'planning_phase'])) === 'replanned' && event.status === 'completed'
  ));
  const stepIds = new Set(steps.map((s) => text(field(s, ['stepId', 'step_id']))));
  const historicalReports = reports.filter((r) => !stepIds.has(text(field(r, ['stepId', 'step_id']))));
  const goalReport = [...reports].reverse().find((r) => arrayFrom(field(r, ['goalChecks', 'goal_checks'])).length > 0);
  const renderReport = (r: Record<string, unknown>, index: number) => (
    <div key={index} className="space-y-1 border-t border-border/60 pt-2">
      <p>{labels[text(r.status)] ?? text(r.status)} · 版本 {text(field(r, ['planRevision', 'plan_revision'])) || '1'}</p>
      <AssistantMarkdown text={text(field(r, ['completedSummary', 'completed_summary']))} />
      {arrayFrom(field(r, ['criteriaChecks', 'criteria_checks'])).map((raw, j) => {
        const check = record(raw);
        return <p key={j}>{check.satisfied === true ? '通过' : '未满足'}：{text(check.criterion)} — {text(check.explanation)}</p>;
      })}
      <p>关联证据：{stringList(field(r, ['evidenceIds', 'evidence_ids'])).join('、') || '无'}</p>
      {text(field(r, ['nextStepHint', 'next_step_hint'])) ? <p>{text(field(r, ['nextStepHint', 'next_step_hint']))}</p> : null}
    </div>
  );
  return <Card padding="none" className="rounded-xl p-3" title="计划与步骤核验" subtitle="任务判断、真实步骤报告与计划调整记录">
    <div className="space-y-3 text-sm leading-6">
      <p>{labels[planning.status] ?? planning.status}{planning.revision > 0 ? ` · 计划版本 ${planning.revision}` : ''}</p>
      {planning.decision?.reason ? <p>{planning.decision.reason}</p> : null}
      {text(plan.goal) ? <p>{text(plan.goal)}</p> : null}
      {planning.error ? <p className="text-warning">{planning.error}</p> : null}
      {goalReport ? <details className="rounded-lg border border-border/70 p-3">
        <summary className="cursor-pointer font-medium">最近一次总体目标核验</summary>
        {arrayFrom(field(goalReport, ['goalChecks', 'goal_checks'])).map((raw, index) => {
          const check = record(raw);
          return <div key={index} className="mt-2">
            <p>{check.satisfied === true ? '通过' : '未满足'}：{text(check.criterion)} — {text(check.explanation)}</p>
            <p>关联证据：{stringList(field(check, ['evidenceIds', 'evidence_ids'])).join('、') || '无'}</p>
          </div>;
        })}
      </details> : null}
      {steps.map((step, index) => {
        const id = text(field(step, ['stepId', 'step_id']));
        const related = reports.filter((r) => text(field(r, ['stepId', 'step_id'])) === id);
        return <details key={id || index} className="rounded-lg border border-border/70 p-3">
          <summary className="cursor-pointer font-medium">{index + 1}. {text(step.objective)} · {labels[text(step.status)] ?? text(step.status)}</summary>
          <div className="mt-2 space-y-2">
            <p>完成条件：{stringList(field(step, ['completionCriteria', 'completion_criteria'])).join('；')}</p>
            {related.map(renderReport)}
          </div>
        </details>;
      })}
      {historicalReports.length > 0 ? <details className="rounded-lg border border-border/70 p-3">
        <summary className="cursor-pointer font-medium">被替换步骤的历史报告</summary>
        {historicalReports.map(renderReport)}
      </details> : null}
      {revisions.map((event, index) => <details key={index} className="rounded-lg border border-border/70 p-3">
        <summary className="cursor-pointer">计划调整 · 版本 {text(event.details?.revision)}</summary>
        <p>{text(event.details?.reason)}</p>
        <p>保留步骤：{stringList(field(event.details, ['completedStepIds', 'completed_step_ids'])).join('、') || '无'}</p>
        <p>调整后步骤：{arrayFrom(field(event.details, ['replacementSteps', 'replacement_steps'])).map((s) => text(record(s).objective)).filter(Boolean).join('；')}</p>
      </details>)}
    </div>
  </Card>;
}

export function GoalAuditCard({ goal }: { goal?: AgentGoalTrace | null }) {
  if (!goal) return null;
  const status = text(goal.status) || 'pending';
  const statusLabel = status === 'completed'
    ? '目标完成'
    : status === 'blocked'
      ? '目标阻塞'
      : status === 'failed'
        ? '目标失败'
        : status === 'waiting_for_user'
          ? '等待用户确认'
          : status === 'replanning'
            ? '重新调整动作'
            : '执行中';
  const statusTone = status === 'completed' ? 'success' : status === 'failed' || status === 'blocked' ? 'danger' : status === 'waiting_for_user' ? 'warning' : 'info';
  const criteria = (goal.criteria ?? []).map((criterion) => ({
    ...criterion,
    status: text(criterion.status) || 'pending',
  }));
  const satisfiedCount = criteria.filter((criterion) => criterion.status === 'satisfied').length;
  const terminal = ['completed', 'blocked', 'failed', 'cancelled'].includes(status);
  const action = terminal ? goal.lastAction : goal.currentAction;
  const actionStatus = text(action?.status) || 'selected';
  const actionStatusLabel = actionStatus === 'failed'
    ? '未能执行'
    : actionStatus === 'blocked'
      ? '等待处理'
      : actionStatus === 'completed'
        ? '已执行'
        : actionStatus === 'waiting_for_user'
          ? '等待用户回复'
          : '已选定';
  const limits = goal.limits ?? {};
  return <Card padding="none" className="rounded-xl p-3" title="Goal 目标执行" subtitle="独立目标合同、证据门禁与受限动作循环">
    <div className="space-y-3 text-sm leading-6">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Metric icon={<Activity className="size-3.5 text-cyan" />} label="状态" value={statusLabel} />
        <Metric icon={<CheckCircle2 className="size-3.5 text-success" />} label="完成条件" value={`${satisfiedCount} / ${criteria.length}`} />
        <Metric icon={<Database className="size-3.5 text-emerald-600" />} label="迭代" value={`${numberValue(goal.iteration) ?? 0} / ${numberValue(limits.iterations) ?? 0}`} />
        <Metric icon={<LoaderCircle className="size-3.5 text-warning" />} label="重规划" value={`${numberValue(goal.replanCount) ?? 0} / ${numberValue(limits.replans) ?? 0}`} />
      </div>
      <p className="text-xs text-secondary-text"><Badge variant={statusTone}>{statusLabel}</Badge>{goal.revision ? ` · 目标版本 ${goal.revision}` : ''}</p>
      {text(goal.objective) ? <p className="text-xs text-foreground/85">目标：{text(goal.objective)}</p> : null}
      {text(goal.progress) ? <p className="text-xs text-secondary-text">进度：{text(goal.progress)}</p> : null}
      {action ? <div className="rounded-lg border border-border/70 p-2.5 text-xs">
        <p className="font-medium text-foreground">{terminal ? '最近动作' : '当前动作'}：{text(action.toolName) || text(action.kind) || '未命名动作'} · {actionStatusLabel}</p>
        {text(action.rationale) ? <p className="mt-1 text-secondary-text">{text(action.rationale)}</p> : null}
      </div> : null}
      {numberValue(limits.actionValidationRepairs) ? <p className="text-xs text-secondary-text">工具动作参数修正：本次运行累计 {numberValue(limits.actionValidationRepairs)} 次（单个动作最多 {numberValue(limits.actionValidationRepairLimit) ?? 0} 次）</p> : null}
      {criteria.length > 0 ? <details open={status !== 'completed'} className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">完成条件与证据（{satisfiedCount} / {criteria.length}）</summary>
        <div className="mt-2 space-y-2 text-xs text-foreground/80">
          {criteria.map((criterion, index) => (
            <div key={criterion.criterionId || index} className="border-t border-border/60 pt-1.5 first:border-t-0 first:pt-0">
              <p className={criterion.status === 'satisfied' ? 'text-success' : criterion.status === 'failed' ? 'text-danger' : 'text-warning'}>
                {criterion.status === 'satisfied' ? '已满足' : criterion.status === 'failed' ? '失败' : criterion.status === 'unverifiable' ? '无法验证' : '待处理'}：{text(criterion.description) || `条件 ${index + 1}`}
              </p>
              <p>验证方式：{text(criterion.verificationMethod) || '未说明'} · 必需条件：{criterion.required ? '是' : '否'}</p>
              <p>关联证据：{stringList(criterion.evidenceIds).join('、') || '无'}</p>
              {text(criterion.explanation) ? <p>{text(criterion.explanation)}</p> : null}
            </div>
          ))}
        </div>
      </details> : null}
      {goal.evidenceIds?.length ? <p className="text-xs text-secondary-text">已收集证据：{goal.evidenceIds.join('、')}</p> : null}
      {text(goal.blocker) ? <p className="rounded-lg border border-warning/30 bg-warning/5 px-2.5 py-2 text-xs text-warning">阻塞原因：{text(goal.blocker)}</p> : null}
      {text(goal.terminalReason) ? <p className="text-xs text-secondary-text">终态说明：{text(goal.terminalReason)}</p> : null}
    </div>
  </Card>;
}

export function TeamAuditCard({ team }: { team?: AgentTeamTrace | null }) {
  if (!team) return null;
  const plan = record(team.plan);
  const tasks = arrayFrom(plan.tasks).map(record);
  const results = arrayFrom(team.results).map(record);
  const resultFor = (taskId: string) => results.find((item) => text(field(item, ['taskId', 'task_id'])) === taskId);
  const executionStrategy = text(team.executionStrategy);
  const requestedMode = text(team.agentMode);
  const resolvedMode = text(team.resolvedAgentMode)
    || (executionStrategy === 'team' ? 'team' : text(team.route) === 'planned' ? 'plan' : text(team.route));
  const routeLabel = resolvedMode
    ? requestedMode === 'auto' && requestedMode !== resolvedMode
      ? `Auto → ${agentModeLabel(resolvedMode)}`
      : agentModeLabel(resolvedMode)
    : '未完成';
  const status = text(team.status);
  const statusLabel = status === 'completed'
    ? '协作完成'
    : status === 'partial' || status === 'workers_partial'
      ? '带缺口完成'
      : status === 'failed'
        ? '协作失败'
        : status || '执行中';
  const statusTone = status === 'completed' ? 'success' : status === 'failed' ? 'danger' : status === 'partial' || status === 'workers_partial' ? 'warning' : 'info';
  const review = record(team.review);
  const reviewLabel = text(review.verdict) === 'pass'
    ? '复核通过'
    : text(review.verdict) === 'block'
      ? '复核阻断'
      : text(review.verdict) === 'revise'
        ? '需要收窄'
        : text(team.reviewStatus) || '尚未复核';
  const conflict = record(team.conflict);
  const bullCase = record(team.bullCase);
  const bearCase = record(team.bearCase);
  const consensus = record(team.consensus);
  const criteriaAssessment = record(team.criteriaAssessment);
  const criteriaChecks = arrayFrom(criteriaAssessment.checks).map(record);
  const criteriaStatus = text(team.criteriaStatus);
  const criteriaLabel = criteriaStatus === 'passed'
    ? '全部通过'
    : criteriaStatus === 'partial'
      ? '部分通过'
      : criteriaStatus === 'blocked'
        ? '核验阻断'
        : criteriaStatus || '尚未核验';
  const criteriaPassedCount = criteriaChecks.filter((check) => text(check.verdict) === 'pass').length;
  const workerHandoff = record(team.workerHandoff);
  const workerHandoffStatus = text(field(workerHandoff, ['status'])) || 'not_started';
  const failurePolicy = record(team.failurePolicy);
  const failurePolicyAction = text(field(failurePolicy, ['action']));
  const failurePolicyStatus = text(field(failurePolicy, ['status'])) || 'not_started';
  const failurePolicyActionLabel: Record<string, string> = {
    retry: '只重试失败方向',
    dispatch: '继续调度就绪方向',
    partial: '保留部分结果后继续',
    replan: '回接 PlanningCoordinator',
    abort: '终止 Team 协作',
    merge: '进入证据合并',
  };
  const workerHandoffLabel = workerHandoffStatus === 'completed'
    ? '交接完成'
    : workerHandoffStatus === 'partial'
      ? '交接不完整'
      : workerHandoffStatus || '尚未交接';
  const failurePolicyLabel = failurePolicyActionLabel[failurePolicyAction]
    || failurePolicyAction
    || '尚未决策';
  return <Card padding="none" className="rounded-xl p-3" title="多智能体协作" subtitle="路由、领域 worker、交接复核与最终综合的完整账本">
    <div className="space-y-3 text-sm leading-6">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Metric icon={<Activity className="size-3.5 text-cyan" />} label="协作路由" value={routeLabel} />
        <Metric icon={<Users className="size-3.5 text-purple" />} label="worker 完成" value={`${numberValue(team.completedWorkerCount) ?? 0} / ${numberValue(team.workerCount) ?? tasks.length}`} />
        <Metric icon={<CheckCircle2 className="size-3.5 text-success" />} label="交接复核" value={reviewLabel} />
        <Metric icon={<Database className="size-3.5 text-emerald-600" />} label="状态" value={statusLabel} />
      </div>
      <p className="text-xs text-secondary-text">当前协作状态：<Badge variant={statusTone}>{statusLabel}</Badge></p>
      {text(team.routeReason) ? <p className="text-xs text-secondary-text">路由说明：{text(team.routeReason)}</p> : null}
      {text(team.executionStrategy) ? <p className="text-xs text-secondary-text">执行路径：{text(team.executionStrategy) === 'team' ? '多 Agent 协作' : '单 Agent 计划步骤'}</p> : null}
      {team.reviewError ? <p className="text-xs text-warning">复核说明：{text(team.reviewError)}</p> : null}
      {text(team.evidenceMergeStatus) ? <div className="text-xs text-secondary-text">
        <p>证据合并：{text(team.evidenceMergeStatus)} · {text(field(team.evidenceMerge, ['summary']))}</p>
        {stringList(field(team.evidenceMerge, ['invalidEvidenceIds', 'invalid_evidence_ids'])).length > 0 ? <p className="text-danger">已拒绝无效证据引用：{stringList(field(team.evidenceMerge, ['invalidEvidenceIds', 'invalid_evidence_ids'])).join('、')}</p> : null}
      </div> : null}
      {team.workerHandoff || workerHandoffStatus !== 'not_started' ? <details open={workerHandoffStatus !== 'completed'} className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">WorkerHandoff · {workerHandoffLabel}</summary>
        <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
          <p>已交接任务：{stringList(field(workerHandoff, ['taskIds', 'task_ids'])).join('、') || '无'}</p>
          {text(field(workerHandoff, ['error'])) ? <p className="text-danger">交接错误：{text(field(workerHandoff, ['error']))}</p> : null}
        </div>
      </details> : null}
      {team.failurePolicy || failurePolicyAction || failurePolicyStatus !== 'not_started' ? <details open={failurePolicyAction !== 'merge'} className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">WorkerFailurePolicy · {failurePolicyLabel}</summary>
        <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
          <p>策略状态：{failurePolicyStatus}</p>
          <p>关联任务：{stringList(field(failurePolicy, ['taskIds', 'task_ids'])).join('、') || '无'}</p>
          {text(field(failurePolicy, ['error'])) ? <p className="text-danger">策略说明：{text(field(failurePolicy, ['error']))}</p> : null}
        </div>
      </details> : null}
      {team.planningHandoffStatus ? <details open={team.planningHandoffStatus !== 'completed'} className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">PlanningCoordinator 回接 · {team.planningHandoffStatus === 'completed' ? '目标检查通过' : '仍有缺口'}</summary>
        <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
          {text(team.planningHandoffError) ? <p className="text-danger">回接错误：{text(team.planningHandoffError)}</p> : null}
          {text(field(team.planningHandoff, ['planningStatus', 'planning_status'])) ? <p>计划状态：{text(field(team.planningHandoff, ['planningStatus', 'planning_status']))}</p> : null}
          <p>重规划次数：{numberValue(field(team.planningHandoff, ['planningReplanCount', 'planning_replan_count'])) ?? 0}</p>
        </div>
      </details> : null}
      {criteriaChecks.length > 0 || criteriaStatus ? <details open={criteriaStatus !== 'passed'} className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">计划完成条件 · {criteriaLabel}（{criteriaPassedCount} / {criteriaChecks.length}）</summary>
        <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
          {text(team.criteriaError) ? <p className="text-danger">核验错误：{text(team.criteriaError)}</p> : null}
          {criteriaChecks.map((check, index) => {
            const verdict = text(check.verdict);
            return <div key={index} className="border-t border-border/60 pt-1 first:border-t-0 first:pt-0">
              <p className={verdict === 'pass' ? 'text-success' : verdict === 'unknown' ? 'text-danger' : 'text-warning'}>
                {verdict === 'pass' ? '通过' : verdict === 'unknown' ? '无法确认' : '未满足'}：{text(check.criterion) || `条件 ${index + 1}`}
              </p>
              <p>{text(check.explanation) || '没有记录核验说明。'}</p>
              <p>关联证据：{stringList(field(check, ['evidenceIds', 'evidence_ids'])).join('、') || '无'}</p>
            </div>;
          })}
        </div>
      </details> : null}
      {text(conflict.status) ? <details className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">冲突与风险门禁 · {text(conflict.status)}</summary>
        <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
          {text(conflict.reason) ? <p>{text(conflict.reason)}</p> : null}
          {arrayFrom(conflict.issues).map((rawIssue, index) => {
            const issue = record(rawIssue);
            return <p key={index} className={text(issue.severity) === 'high' ? 'text-danger' : 'text-warning'}>
              {text(issue.category) || '风险'}：{text(issue.reason) || '需要进一步核对'}
            </p>;
          })}
        </div>
      </details> : null}
      {text(plan.goal) ? <p className="text-xs text-foreground/85">协作目标：{text(plan.goal)}</p> : null}
      {tasks.length > 0 ? <div className="space-y-1.5">
        <p className="text-[11px] font-semibold text-foreground">领域任务与交接</p>
        {tasks.map((task, index) => {
          const taskId = text(field(task, ['taskId', 'task_id']));
          const result = resultFor(taskId);
          const resultStatus = text(field(result, ['status'])) || 'pending';
          const resultLabel = resultStatus === 'completed' ? '已完成' : resultStatus === 'failed' ? '失败' : resultStatus === 'partial' ? '部分完成' : '未完成';
          const resultTone = resultStatus === 'completed' ? 'success' : resultStatus === 'failed' ? 'danger' : resultStatus === 'partial' ? 'warning' : 'info';
          const evidenceIds = stringList(field(result, ['evidenceIds', 'evidence_ids']));
          const workerCriteriaChecks = arrayFrom(field(result, ['criteriaChecks', 'criteria_checks'])).map(record);
          const workerCriteriaStatus = text(field(result, ['criteriaStatus', 'criteria_status']));
          return <details key={taskId || index} className="rounded-lg border border-border/70 p-2.5">
            <summary className="cursor-pointer font-medium">
              {index + 1}. {text(field(task, ['role'])) || '领域 worker'} · {text(field(task, ['objective'])) || taskId || '未命名任务'}
              <span className="ml-1.5"><Badge variant={resultTone}>{resultLabel}</Badge></span>
            </summary>
            <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
              <p>授权工具：{stringList(field(task, ['allowedTools', 'allowed_tools'])).join('、') || '无'}</p>
              {stringList(field(task, ['inputContext', 'input_context'])).length > 0 ? <p>输入上下文：{stringList(field(task, ['inputContext', 'input_context'])).join('；')}</p> : null}
              <p>输出格式：{text(field(task, ['outputFormat', 'output_format'])) || '结构化领域观察'}</p>
              <p>超时与失败策略：{numberValue(field(task, ['timeoutSeconds', 'timeout_seconds'])) ?? 0} 秒 · {text(field(task, ['failureStrategy', 'failure_strategy'])) || 'partial'}</p>
              {stringList(field(task, ['dependsOn', 'depends_on'])).length > 0 ? <p>前置任务：{stringList(field(task, ['dependsOn', 'depends_on'])).join('、')}</p> : null}
              <p>任务完成条件：{stringList(field(task, ['successCriteria', 'success_criteria'])).join('；') || '未记录'}</p>
              <p>需要核验的证据：{stringList(field(task, ['requiredEvidence', 'required_evidence'])).join('；') || '未记录'}</p>
              {text(field(result, ['agentNode', 'agent_node'])) ? <p>Agent 节点：{text(field(result, ['agentNode', 'agent_node']))}</p> : null}
              <p>执行次数：{numberValue(field(result, ['attempt'])) ?? team.taskAttempts?.[taskId] ?? '尚未执行'}</p>
              {text(field(result, ['summary'])) ? <AssistantMarkdown text={text(field(result, ['summary']))} /> : <p>worker 没有返回结构化总结。</p>}
              {arrayFrom(field(result, ['findings'])).length > 0 ? <p>观察：{arrayFrom(field(result, ['findings'])).map(text).join('；')}</p> : null}
              {arrayFrom(field(result, ['findingEvidenceIds', 'finding_evidence_ids'])).length > 0 ? <p>观察证据映射：{arrayFrom(field(result, ['findingEvidenceIds', 'finding_evidence_ids'])).map((refs) => stringList(refs).join('、') || '无').join('；')}</p> : null}
              {arrayFrom(field(result, ['limitations'])).length > 0 ? <p className="text-warning">限制：{arrayFrom(field(result, ['limitations'])).map(text).join('；')}</p> : null}
              {arrayFrom(field(result, ['openQuestions', 'open_questions'])).length > 0 ? <p className="text-warning">待确认：{arrayFrom(field(result, ['openQuestions', 'open_questions'])).map(text).join('；')}</p> : null}
              <p>证据：{evidenceIds.join('、') || '无可关联证据'} · 工具调用 {numberValue(field(result, ['toolCallCount', 'tool_call_count'])) ?? 0} 次 · 置信度 {text(field(result, ['confidence'])) || '未说明'}</p>
              {workerCriteriaChecks.length > 0 || workerCriteriaStatus ? <div className="mt-2 rounded-md border border-border/60 bg-card/60 p-2">
                <p className="font-medium">任务条件验收：{workerCriteriaStatus === 'passed' ? '全部通过' : workerCriteriaStatus === 'partial' ? '部分通过' : workerCriteriaStatus === 'blocked' ? '核验阻断' : workerCriteriaStatus || '尚未核验'}</p>
                {workerCriteriaChecks.map((check, checkIndex) => {
                  const verdict = text(check.verdict);
                  return <p key={checkIndex} className={verdict === 'pass' ? 'text-success' : 'text-warning'}>
                    {verdict === 'pass' ? '通过' : verdict === 'unknown' ? '无法确认' : '未满足'}：{text(check.criterion) || `条件 ${checkIndex + 1}`} · {text(check.explanation)}
                  </p>;
                })}
              </div> : null}
              {text(field(result, ['errorCode', 'error_code'])) ? <p className="text-danger">错误：{text(field(result, ['errorCode', 'error_code']))}</p> : null}
            </div>
          </details>;
        })}
      </div> : null}
      {review.summary || arrayFrom(review.issues).length > 0 ? <details className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium"><ShieldCheck className="mr-1 inline size-3.5 text-cyan" />交接复核详情</summary>
        <div className="mt-2 space-y-1 text-[11px] text-foreground/80">
          {text(review.summary) ? <p>{text(review.summary)}</p> : null}
          {arrayFrom(review.issues).map((rawIssue, index) => {
            const issue = record(rawIssue);
            return <p key={index} className={text(issue.severity) === 'high' ? 'text-danger' : 'text-warning'}>
              {text(issue.category) || '复核问题'}：{text(issue.reason) || '需要进一步核对'}
            </p>;
          })}
        </div>
      </details> : null}
      {bullCase.summary || bearCase.summary || consensus.conclusion ? <details className="rounded-lg border border-border/70 p-2.5">
        <summary className="cursor-pointer font-medium">多空对抗与共识解析</summary>
        <div className="mt-2 space-y-2 text-[11px] text-foreground/80">
          {bullCase.summary ? <div><p className="font-medium">看多审查 · {text(bullCase.confidence) || '未说明'}</p><p>{text(bullCase.summary)}</p><p>支持证据：{stringList(bullCase.supportingEvidenceIds ?? bullCase.supporting_evidence_ids).join('、') || '无'}</p></div> : null}
          {bearCase.summary ? <div><p className="font-medium">看空审查 · {text(bearCase.confidence) || '未说明'}</p><p>{text(bearCase.summary)}</p><p>支持证据：{stringList(bearCase.supportingEvidenceIds ?? bearCase.supporting_evidence_ids).join('、') || '无'}</p></div> : null}
          {consensus.conclusion ? <div className="border-t border-border/60 pt-2"><p className="font-medium">共识：{text(consensus.verdict)} · {text(consensus.confidence) || '未说明'}</p><p>{text(consensus.conclusion)}</p><p>{text(consensus.rationale)}</p><p>{consensus.allowFinalAnswer === true ? '允许进入最终回答' : '未允许直接进入最终回答'} · {consensus.needsReplan === true ? '需要重规划' : '无需重规划'}</p></div> : null}
        </div>
      </details> : null}
    </div>
  </Card>;
}
