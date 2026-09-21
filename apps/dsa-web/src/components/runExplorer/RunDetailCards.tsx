import { useState } from 'react';
import type React from 'react';
import { Activity, CheckCircle2, Copy, Database, Link2, LoaderCircle, ShieldCheck, TriangleAlert, Users } from 'lucide-react';
import type { AgentBehaviorAudit, AgentSourceSampleResponse } from '../../api/runExplorer';
import type { AgentPlanningTrace, AgentTeamTrace } from '../../api/agent';
import { AssistantMarkdown } from '../assistant-ui/AssistantMarkdownText';
import { Badge, Card } from '../common';
import { cn } from '../../utils/cn';
import { agentModeLabel } from '../../utils/agentMode';
import {
  accessModeFor,
  arrayFrom,
  displayDataTime,
  errorStringList,
  field,
  hasValue,
  isHttpUrl,
  jsonString,
  numberValue,
  previewJson,
  record,
  sourceAttemptList,
  sourceLabel,
  stringList,
  text,
  uniqueStrings,
} from './RunDetailUtils';

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

export function ToolObservationDetails({
  result,
  step,
  actionSources,
  payloadsLoaded,
  payloadsLoading,
  onLoadPayloads,
}: {
  result: Record<string, unknown>;
  step?: Record<string, unknown>;
  actionSources: string[];
  payloadsLoaded: boolean;
  payloadsLoading: boolean;
  onLoadPayloads?: () => void;
}) {
  const stepResult = field(step, ['result']);
  const rawProjectedResult = field(result, ['result', 'response']);
  const displayProjectedResult = field(result, ['displayResult', 'display_result']);
  const projectedResult = rawProjectedResult ?? displayProjectedResult;
  const response = hasValue(stepResult) ? stepResult : projectedResult;
  const responseRecord = record(response);
  const stepArguments = field(step, ['arguments']);
  const request = hasValue(stepArguments)
    ? stepArguments
    : field(result, ['arguments', 'request', 'input']);
  const rawResponse = hasValue(stepResult) ? stepResult : hasValue(projectedResult) ? projectedResult : result;
  const fullResponseLoaded = hasValue(stepResult) || hasValue(rawProjectedResult);
  const toolName = text(field(result, ['toolName', 'tool_name']));
  const accessMode = accessModeFor(toolName, result);
  const accessRecord = record(
    field(responseRecord, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit'])
      ?? field(result, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit']),
  );
  const contentLength = numberValue(field(accessRecord, ['contentLength', 'content_length']) ?? field(responseRecord, ['contentLength', 'content_length']));
  const extractionMethod = text(field(accessRecord, ['extractionMethod', 'extraction_method']) ?? field(responseRecord, ['extractionMethod', 'extraction_method']));
  const contentRead = field(accessRecord, ['contentRead', 'content_read']);
  const contentExtracted = field(accessRecord, ['contentExtracted', 'content_extracted']);

  const source = text(field(responseRecord, ['source', 'sourceLabel', 'source_label']))
    || text(field(result, ['source', 'sourceLabel', 'source_label']))
    || actionSources[0];
  const sourceKey = text(field(responseRecord, ['sourceKey', 'source_key']))
    || text(field(result, ['sourceKey', 'source_key']));
  const sourceOrigin = text(field(responseRecord, ['sourceOrigin', 'source_origin']))
    || text(field(result, ['sourceOrigin', 'source_origin']));
  const sourceScope = text(field(responseRecord, ['sourceScope', 'source_scope']))
    || text(field(result, ['sourceScope', 'source_scope']));
  const dataTime = field(responseRecord, ['dataTime', 'data_time'])
    ?? field(result, ['dataTime', 'data_time']);
  const dataTimeProvenance = text(field(responseRecord, ['dataTimeProvenance', 'data_time_provenance']))
    || text(field(result, ['dataTimeProvenance', 'data_time_provenance']));
  const rows = arrayFrom(response);
  const count = numberValue(
    field(responseRecord, ['count', 'resultCount', 'result_count', 'total'])
      ?? field(result, ['count', 'resultCount', 'result_count'])
      ?? (rows.length > 0 ? rows.length : undefined),
  );
  const success = field(responseRecord, ['success']) ?? result.success;
  const partial = field(responseRecord, ['partial']) ?? result.partial;
  const fallbackUsed = field(responseRecord, ['fallbackUsed', 'fallback_used'])
    ?? field(result, ['fallbackUsed', 'fallback_used']);
  const fallbackProvider = text(field(responseRecord, ['fallbackProvider', 'fallback_provider']))
    || text(field(result, ['fallbackProvider', 'fallback_provider']));
  const cached = field(responseRecord, ['_cached', 'cached']) ?? field(result, ['_cached', 'cached']);
  const stale = field(responseRecord, ['isStale', 'is_stale']) ?? field(result, ['isStale', 'is_stale']);
  const freshnessUnknown = field(responseRecord, ['freshnessUnknown', 'freshness_unknown'])
    ?? field(result, ['freshnessUnknown', 'freshness_unknown']);
  const barComplete = field(responseRecord, ['barComplete', 'bar_complete'])
    ?? field(result, ['barComplete', 'bar_complete']);
  const warnings = uniqueStrings([
    ...errorStringList(field(responseRecord, ['warnings', 'warning'])),
    ...errorStringList(field(result, ['warnings', 'warning'])),
  ]);
  const errors = uniqueStrings([
    ...errorStringList(field(responseRecord, ['errors', 'error'])),
    ...errorStringList(field(result, ['errors', 'error'])),
  ]);
  const attempts = sourceAttemptList(
    field(responseRecord, ['sourceAttempts', 'source_attempts'])
      ?? field(result, ['sourceAttempts', 'source_attempts']),
  );
  const refs = uniqueStrings([
    ...stringList(field(responseRecord, ['sourceRefs', 'source_refs'])),
    ...stringList(field(result, ['sourceRefs', 'source_refs'])),
  ]);
  const links = uniqueStrings([
    ...stringList(field(responseRecord, ['referenceLinks', 'reference_links'])),
    ...stringList(field(result, ['referenceLinks', 'reference_links'])),
    ...refs,
  ]).filter(isHttpUrl);
  const sampleRows = rows.length > 6
    ? [...rows.slice(0, 3), '… 中间数据省略 …', ...rows.slice(-3)]
    : rows;

  return <div className="mt-2 space-y-2 rounded-lg border border-cyan/20 bg-cyan/5 p-2.5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p className="text-[11px] font-semibold text-foreground">请求与返回审计</p>
        <p className="mt-0.5 text-[10px] text-secondary-text">
          {fullResponseLoaded ? '已加载执行账本中的实际请求与规范化返回。' : '当前先展示运行摘要；点击加载后可核对完整返回。'}
        </p>
      </div>
      {!payloadsLoaded && onLoadPayloads ? <button
        type="button"
        onClick={onLoadPayloads}
        disabled={payloadsLoading}
        className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-[10px] text-secondary-text transition hover:text-foreground disabled:opacity-60"
      >
        <LoaderCircle className={cn('size-3', payloadsLoading && 'animate-spin')} />
        {payloadsLoading ? '加载中…' : '重新加载原始记录'}
      </button> : null}
    </div>

    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
      <AuditField label="实际来源" value={source || '未标注'} />
      <AuditField label="来源标识" value={sourceKey || '未提供'} />
      <AuditField
        label="正文访问"
        value={accessMode === 'reference_only'
          ? '仅来源引用，未访问正文'
          : accessMode === 'content_read'
            ? `${contentRead === false ? '读取失败' : contentExtracted === true ? '已读取并提取' : contentRead === true ? '已读取但未提取正文' : '读取状态未记录'}${contentLength == null ? '' : ` · ${contentLength} 字`}${extractionMethod ? ` · ${extractionMethod}` : ''}`
            : '结构化数据，不适用正文读取'}
        tone={accessMode === 'reference_only' || contentRead === false || (accessMode === 'content_read' && contentExtracted !== true) ? 'warning' : accessMode === 'content_read' ? 'success' : 'neutral'}
      />
      <AuditField label="返回数量" value={count == null ? '未提供' : `${count} 条`} />
      <AuditField label="数据时间" value={dataTime ? `${displayDataTime(dataTime)}${dataTimeProvenance ? `（${dataTimeProvenance}）` : ''}` : '未提供'} />
      <AuditField label="请求完成" value={booleanLabel(success, '成功', '失败')} tone={success === true ? 'success' : success === false ? 'danger' : 'neutral'} />
      <AuditField label="数据状态" value={
        fallbackUsed === true
          ? `已降级${fallbackProvider ? `：${fallbackProvider}` : ''}`
          : stale === true
            ? '过期数据'
            : freshnessUnknown === true
              ? '新鲜度未知'
              : partial === true || barComplete === false
                ? '不完整'
                : '未发现异常标记'
      } tone={fallbackUsed === true || stale === true || freshnessUnknown === true || partial === true || barComplete === false ? 'warning' : 'neutral'} />
    </div>
    {sourceOrigin || sourceScope || cached === true ? <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-secondary-text">
      {sourceOrigin ? <span>原始来源：{sourceOrigin}</span> : null}
      {sourceScope ? <span>来源范围：{sourceScope}</span> : null}
      {cached === true ? <span>本次使用缓存</span> : null}
    </div> : null}

    {attempts.length > 0 ? <div className="rounded-md border border-border/70 bg-card/70 p-2">
      <p className="text-[10px] font-semibold text-foreground">来源尝试链路</p>
      <div className="mt-1 space-y-1">
        {attempts.map((attempt, index) => <div key={`${text(attempt.source) || text(attempt.label) || 'source'}-${index}`} className="flex flex-wrap items-start justify-between gap-2 text-[10px]">
          <span className="font-medium">{text(attempt.label) || text(attempt.source) || `来源 ${index + 1}`}</span>
          <span className={cn('text-right', text(attempt.status) === 'success' ? 'text-success' : 'text-danger')}>
            {text(attempt.status) === 'success' ? `成功${numberValue(attempt.count) == null ? '' : ` · ${numberValue(attempt.count)} 条`}` : text(attempt.error) || text(attempt.errorType) || text(attempt.error_type) || '未返回'}
          </span>
        </div>)}
      </div>
    </div> : null}

    {warnings.length > 0 ? <NoticeList title="返回警告" items={warnings} tone="warning" /> : null}
    {errors.length > 0 ? <NoticeList title="返回错误" items={errors} tone="danger" /> : null}
    <JsonBlock title="实际请求参数" value={request} empty="执行账本没有保存可展示的请求参数。" />
    {sampleRows.length > 0 ? <JsonBlock title={`返回数据样本（${rows.length} 条中展示 ${Math.min(rows.length, 6)} 条）`} value={sampleRows} /> : null}
    <JsonBlock title={fullResponseLoaded ? '规范化返回 JSON（已脱敏）' : '返回摘要 JSON'} value={rawResponse} empty="该工具没有返回可展示的响应体。" />
    {links.length > 0 ? <div className="rounded-md border border-border/70 bg-card/70 p-2">
      <p className="text-[10px] font-semibold text-foreground">{accessMode === 'reference_only' ? '来源引用（不代表已访问）' : accessMode === 'content_read' ? '已读取来源' : '来源链接'}</p>
      <div className="mt-1 space-y-0.5 border-l border-border pl-2">
        {links.map((link) => <a key={link} href={link} target="_blank" rel="noreferrer" className="block break-all text-[10px] text-cyan hover:underline">{link}</a>)}
      </div>
    </div> : refs.length > 0 ? <p className="text-[10px] text-secondary-text">来源引用：{refs.join('、')}</p> : null}
  </div>;
}

function booleanLabel(value: unknown, yes: string, no: string) {
  if (value === true || text(value).toLowerCase() === 'true') return yes;
  if (value === false || text(value).toLowerCase() === 'false') return no;
  return '未说明';
}

function AuditField({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}) {
  return <div className="rounded-md border border-border/70 bg-card/70 px-2 py-1.5">
    <p className="text-[10px] text-secondary-text">{label}</p>
    <p className={cn('mt-0.5 line-clamp-2 break-all text-[11px] font-medium', tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : tone === 'danger' ? 'text-danger' : 'text-foreground')} title={value}>{value}</p>
  </div>;
}

function NoticeList({ title, items, tone }: { title: string; items: string[]; tone: 'warning' | 'danger' }) {
  return <div className={cn('rounded-md border px-2 py-1.5', tone === 'danger' ? 'border-danger/25 bg-danger/5' : 'border-warning/25 bg-warning/5')}>
    <p className={cn('text-[10px] font-semibold', tone === 'danger' ? 'text-danger' : 'text-warning')}>{title}</p>
    <ul className="mt-1 space-y-0.5 pl-3 text-[10px] leading-4 text-foreground/80">
      {items.map((item, index) => <li key={`${item}-${index}`} className="list-disc break-words">{item}</li>)}
    </ul>
  </div>;
}

function JsonBlock({ title, value, empty = '没有可展示的数据。' }: { title: string; value: unknown; empty?: string }) {
  const [copied, setCopied] = useState(false);
  const hasContent = hasValue(value);
  const serialized = hasContent ? jsonString(value) : '';
  const copy = async () => {
    if (!serialized || typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(serialized);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return <div className="rounded-md border border-border/70 bg-card/70 p-2">
    <div className="flex items-center justify-between gap-2">
      <p className="text-[10px] font-semibold text-foreground">{title}</p>
      {serialized ? <button type="button" onClick={() => void copy()} className="inline-flex items-center gap-1 text-[10px] text-secondary-text transition hover:text-foreground" title="复制脱敏后的 JSON">
        <Copy className="size-3" />{copied ? '已复制' : '复制 JSON'}
      </button> : null}
    </div>
    {serialized ? <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/60 p-2 font-mono text-[10px] leading-4 text-foreground/80">{previewJson(value)}</pre> : <p className="mt-1.5 text-[10px] text-secondary-text">{empty}</p>}
  </div>;
}

export function ErrorDetails({
  title,
  errorCode,
  details,
  defaultOpen = false,
  fallback,
}: {
  title: string;
  errorCode: string;
  details: string[];
  defaultOpen?: boolean;
  fallback: string;
}) {
  const content = [errorCode ? `错误代码：${errorCode}` : '', ...details].filter(Boolean).join('\n') || fallback;
  return <details open={defaultOpen} className="mt-2 rounded-md border border-danger/25 bg-danger/5 px-2.5 py-2 text-danger">
    <summary className="cursor-pointer select-none text-[11px] font-medium hover:opacity-80">{title}</summary>
    <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-foreground/80">{content}</pre>
  </details>;
}

export function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="rounded-lg bg-muted/60 p-2.5">{icon}<p className="mt-1 text-[11px] text-secondary-text">{label}</p><p className="mt-0.5 text-sm font-medium">{value}</p></div>;
}

export function BehaviorAuditCard({
  audit,
  onSampleSources,
  sourceSampling = false,
  sourceSample,
}: {
  audit?: AgentBehaviorAudit;
  onSampleSources?: () => void;
  sourceSampling?: boolean;
  sourceSample?: AgentSourceSampleResponse | null;
}) {
  if (!audit) return null;
  const actionCount = audit.actionRequiredCount ?? audit.dangerCount + audit.warningCount;
  const advisoryCount = audit.advisoryCount ?? audit.infoCount ?? 0;
  const actionableFindings = audit.findings.filter((finding) => finding.disposition !== 'advisory');
  const advisoryFindings = audit.findings.filter((finding) => finding.disposition === 'advisory');
  const statusLabel = audit.status === 'danger'
    ? '存在需要处理的执行或数据问题'
    : audit.status === 'warning'
      ? '有需要处理的核对问题'
      : audit.status === 'info'
        ? '运行完成，有观察提示'
        : '自动核对通过';
  const statusTone = audit.status === 'danger' ? 'danger' : audit.status === 'warning' ? 'warning' : audit.status === 'info' ? 'info' : 'success';
  const statusIcon = audit.status === 'clear'
    ? <ShieldCheck className="size-4 text-success" />
    : audit.status === 'info'
      ? <Activity className="size-4 text-cyan" />
      : <TriangleAlert className={cn('size-4', audit.status === 'danger' ? 'text-danger' : 'text-warning')} />;
  return <div id="run-behavior-audit" className="scroll-mt-3">
      <Card padding="none" className="rounded-xl p-3" title="执行诊断" subtitle="根据调用记录核对执行、数据和证据异常，并保留恢复情况">
      <div className={cn('flex items-start justify-between gap-2 rounded-lg border px-3 py-2.5', audit.status === 'danger' ? 'border-danger/25 bg-danger/5' : audit.status === 'warning' ? 'border-warning/25 bg-warning/8' : audit.status === 'info' ? 'border-cyan/25 bg-cyan/5' : 'border-success/25 bg-success/8')}>
        <div className="flex min-w-0 items-start gap-2">
          {statusIcon}
          <div className="min-w-0">
            <p className={cn('text-xs font-semibold', audit.status === 'danger' ? 'text-danger' : audit.status === 'warning' ? 'text-warning' : audit.status === 'info' ? 'text-cyan' : 'text-success')}>{statusLabel}</p>
            <p className="mt-0.5 text-[11px] leading-5 text-foreground/75">
              {actionCount > 0
                ? `发现 ${actionCount} 个需要处理的核对项（高风险 ${audit.dangerCount}，待核对 ${Math.max(0, actionCount - audit.dangerCount)}）。`
                : `当前没有需要人工处理的异常${advisoryCount ? `，保留 ${advisoryCount} 条观察提示供排查。` : '。'} `}
              {actionCount > 0
                ? '请核对下方异常项及其对应调用。'
                : '自动核对未发现待处理异常，不代表回答中的事实已经全部验证。'}
            </p>
          </div>
        </div>
        <Badge variant={statusTone}>{audit.riskScore} 风险分</Badge>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Metric icon={<Activity className="size-3.5 text-cyan" />} label="模型轮次 / 工具调用" value={`${audit.modelTurnCount} / ${audit.toolCallCount}`} />
        <Metric icon={<Link2 className="size-3.5 text-purple" />} label="候选 / 未读取（未选不等于失败）" value={`${audit.referenceLinkCount} / ${audit.unreadReferenceCount}`} />
        <Metric icon={<Database className="size-3.5 text-emerald-600" />} label="正文读取 / 提取" value={`${audit.contentReadCallCount} / ${audit.contentExtractedCallCount}`} />
        <Metric icon={<CheckCircle2 className="size-3.5 text-success" />} label="证据 / 结论" value={`${audit.evidenceCount} / ${audit.claimCount}`} />
      </div>
      {actionableFindings.length > 0 ? <FindingList title={`需要处理（${actionableFindings.length}）`} findings={actionableFindings} /> : null}
      {advisoryFindings.length > 0 ? <FindingList title={`自动观察（${advisoryFindings.length}，不阻断本次运行）`} findings={advisoryFindings} advisory /> : null}
      <details className="mt-2 rounded-md border border-border/70 bg-card/60 px-2.5 py-2">
        <summary className="cursor-pointer select-none text-[10px] font-medium text-secondary-text hover:text-foreground">查看全部自动检查项</summary>
        <div className="mt-1.5 grid gap-1 sm:grid-cols-2">{audit.checks.map((check) => <div key={check.code} className="flex items-start gap-1.5 text-[10px]">
          <span className={cn('mt-0.5 size-1.5 shrink-0 rounded-full', check.status === 'danger' ? 'bg-danger' : check.status === 'warning' ? 'bg-warning' : check.status === 'info' ? 'bg-cyan' : 'bg-success')} />
          <span><span className="font-medium text-foreground">{check.label}</span><span className="ml-1 text-secondary-text">{check.detail}</span></span>
        </div>)}</div>
      </details>
      {audit.sampling?.available && onSampleSources ? <div className="mt-2 rounded-lg border border-cyan/20 bg-cyan/5 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold text-foreground">系统自动抽检</p>
            <p className="mt-0.5 text-[10px] text-secondary-text">抽检最多 {audit.sampling.sampleLimit} 条来源，只验证链接可访问和正文能否提取，不代表模型原分析时阅读过。</p>
          </div>
          <button type="button" onClick={onSampleSources} disabled={sourceSampling} className="inline-flex items-center gap-1 rounded-md border border-cyan/30 bg-card px-2 py-1 text-[10px] font-medium text-cyan transition hover:bg-cyan/8 disabled:opacity-60">
            {sourceSampling ? <LoaderCircle className="size-3 animate-spin" /> : <Link2 className="size-3" />}
            {sourceSampling ? '抽检中…' : '抽检来源'}
          </button>
        </div>
        {sourceSample ? <div className="mt-2 space-y-1 border-t border-cyan/15 pt-2">
          {sourceSample.items.map((item) => <div key={item.url} className="rounded-md bg-card/70 px-2 py-1.5 text-[10px]">
            <div className="flex items-start justify-between gap-2"><span className="min-w-0 break-all text-foreground"><span className="mr-1 text-secondary-text">{item.kind === 'document' ? 'PDF/文档' : '文章'}</span>{item.url}</span><span className={item.success ? 'shrink-0 text-success' : 'shrink-0 text-danger'}>{item.success ? '可读取' : '不可读取'}</span></div>
            <p className="mt-0.5 text-secondary-text">{item.contentLength ? `${item.contentLength} 字` : '无正文'}{item.extractionMethod ? ` · ${item.extractionMethod}` : ''}{item.errors?.length ? ` · ${item.errors[0]}` : ''}</p>
            {item.contentPreview ? <details className="mt-1"><summary className="cursor-pointer text-secondary-text">查看抽检正文预览</summary><p className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words text-foreground/75">{item.contentPreview}</p></details> : null}
          </div>)}
          <p className="text-[10px] text-secondary-text">{sourceSample.note}</p>
        </div> : null}
      </div> : null}
    </Card>
  </div>;
}

function FindingList({
  title,
  findings,
  advisory = false,
}: {
  title: string;
  findings: AgentBehaviorAudit['findings'];
  advisory?: boolean;
}) {
  return <div className="mt-2 space-y-1.5">
    <p className={cn('text-[11px] font-semibold', advisory ? 'text-cyan' : 'text-foreground')}>{title}</p>
    {findings.map((finding) => {
      const tone = advisory ? 'info' : finding.severity;
      return <div key={`${finding.code}-${finding.title}`} className={cn('rounded-lg border px-3 py-2', tone === 'danger' ? 'border-danger/20 bg-danger/5' : tone === 'info' ? 'border-cyan/20 bg-cyan/5' : 'border-warning/20 bg-warning/8')}>
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-start gap-2">
            {tone === 'info' ? <Activity className="mt-0.5 size-3.5 shrink-0 text-cyan" /> : <TriangleAlert className={cn('mt-0.5 size-3.5 shrink-0', tone === 'danger' ? 'text-danger' : 'text-warning')} />}
            <div className="min-w-0">
              <p className={cn('text-xs font-medium', tone === 'danger' ? 'text-danger' : tone === 'info' ? 'text-cyan' : 'text-warning')}>{finding.title}</p>
              <p className="mt-0.5 text-[11px] leading-5 text-foreground/75">{finding.detail}</p>
              {finding.remediation ? <p className="mt-1 text-[10px] leading-4 text-secondary-text">建议：{finding.remediation}</p> : null}
            </div>
          </div>
          <button type="button" onClick={() => document.getElementById('run-tools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })} className="shrink-0 rounded-md border border-border/70 bg-card px-2 py-1 text-[10px] text-secondary-text transition hover:text-foreground">查看调用链</button>
        </div>
        {finding.links && finding.links.length > 0 ? <details className="mt-1.5 pl-5 text-[10px] text-secondary-text">
          <summary className="cursor-pointer select-none hover:text-foreground">查看 {finding.links.length} 条关联来源（仅展示，不代表已读取）</summary>
          <div className="mt-1 space-y-0.5 border-l border-border pl-2">{finding.links.map((link) => <a key={link} href={link} target="_blank" rel="noreferrer" className="block break-all text-cyan hover:underline">{link}</a>)}</div>
        </details> : null}
      </div>;
    })}
  </div>;
}

export function ClaimEvidenceCard({ claims }: { claims: Array<Record<string, unknown>> }) {
  if (claims.length === 0) return null;
  const checkLabels: Record<string, string> = {
    referenceIntegrity: '引用有效性',
    reference_integrity: '引用有效性',
    tool_success: '工具成功',
    entity_scope: '主体',
    toolSuccess: '工具成功',
    source: '来源',
    entityScope: '主体',
    time: '时间',
  };
  return <Card padding="none" className="rounded-xl p-3" title="回答与证据核对" subtitle="自动检查结论是否能追溯到工具返回和来源">
    <div className="space-y-1.5">
      {claims.slice(0, 40).map((claim, index) => {
        const checks = record(claim.checks);
        const evidenceIds = stringList(claim.evidenceIds ?? claim.evidence_ids);
        const unresolvedIds = stringList(claim.unresolvedEvidenceIds ?? claim.unresolved_evidence_ids);
        const claimIssues = stringList(claim.issues);
        const requiresEvidence = (claim.requiresEvidence ?? claim.requires_evidence) !== false;
        const failed = Object.values(checks).some((value) => value !== true)
          || unresolvedIds.length > 0 || claimIssues.length > 0 || (requiresEvidence && evidenceIds.length === 0);
        return <div key={text(claim.claimId ?? claim.claim_id) || index} className={cn('rounded-lg border px-2.5 py-2', failed ? 'border-warning/20 bg-warning/8' : 'border-border/70 bg-card/60')}>
          <div className="flex items-start justify-between gap-2">
            <p className="min-w-0 flex-1 line-clamp-3 text-xs leading-5 text-foreground">{text(claim.text) || '未记录结论文本'}</p>
            <Badge variant={failed ? 'warning' : 'success'}>{evidenceIds.length > 0 ? `${evidenceIds.length} 条证据` : requiresEvidence ? '无证据' : '无需引证'}</Badge>
          </div>
          {unresolvedIds.length > 0 ? <p className="mt-1 break-all text-xs text-danger">无效引用：{unresolvedIds.join('、')}</p> : null}
          {claimIssues.map((issue) => <p key={issue} className="mt-1 text-xs text-danger">{issue}</p>)}
          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-secondary-text">
            {Object.entries(checks).map(([key, value]) => <span key={key} className={value === true ? 'text-success' : 'text-danger'}>{checkLabels[key] ?? key}：{value === true ? '通过' : '未通过'}</span>)}
            {evidenceIds.length > 0 ? <span className="font-mono">{evidenceIds.slice(0, 3).join('、')}</span> : null}
          </div>
        </div>;
      })}
    </div>
    {claims.length > 40 ? <p className="mt-1.5 text-[10px] text-secondary-text">其余 {claims.length - 40} 条结论已折叠。</p> : null}
  </Card>;
}

export function EvidenceRow({ item, index }: { item: Record<string, unknown>; index: number }) {
  const refs = stringList(item.sourceRefs);
  const sources = uniqueStrings(refs.map(sourceLabel));
  const urls = refs.filter(isHttpUrl);
  const evidenceId = text(item.evidenceId) || text(item.id) || `证据 ${index + 1}`;
  return <div className="px-2.5 py-2"><div className="flex items-center justify-between gap-2"><span className="line-clamp-2 text-xs font-medium text-foreground" title={refs.join('、')}>{sources.join('、') || text(item.toolName) || '未标注来源'}</span><span className="shrink-0 text-[10px] text-secondary-text">{displayDataTime(item.dataTime)}</span></div><p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={evidenceId}>{evidenceId}</p>{urls.length > 0 ? <details className="mt-1.5 text-[10px] text-secondary-text"><summary className="cursor-pointer select-none hover:text-foreground">查看 {urls.length} 条原始链接</summary><div className="mt-1 space-y-0.5 border-l border-border pl-2">{urls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer" className="block break-all text-cyan hover:underline">{url}</a>)}</div></details> : null}</div>;
}
