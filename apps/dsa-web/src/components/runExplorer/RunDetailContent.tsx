import { useState } from 'react';
import { Activity, CheckCircle2, Clock3, Database, ListChecks, LoaderCircle, ThumbsDown, ThumbsUp, TriangleAlert } from 'lucide-react';
import type { AgentRunDetail, AgentSourceSampleResponse } from '../../api/runExplorer';
import { Badge, Card } from '../common';
import { cn } from '../../utils/cn';
import { agentModeLabel } from '../../utils/agentMode';
import { formatDateTime } from '../../utils/format';
import {
  STATUS_LABELS,
  arrayFrom,
  buildQualityIssues,
  displayDataTime,
  errorCodeFrom,
  errorDetailsFrom,
  field,
  findStepForTool,
  formatDuration,
  hasValue,
  percent,
  record,
  sourceLabel,
  stringList,
  text,
  toolOutcomeFor,
  toolOutcomeLabel,
  toolOutcomeVariant,
  uniqueStrings,
  statusVariant,
  type QualityIssue,
} from './RunDetailUtils';
import {
  BehaviorAuditCard,
  ClaimEvidenceCard,
  ErrorDetails,
  EvidenceRow,
  GoalAuditCard,
  Metric,
  PlanningAuditCard,
  TeamAuditCard,
  ToolObservationDetails,
} from './RunDetailCards';

type RunDetailContentProps = {
  detail: AgentRunDetail;
  onFeedback: (rating: -1 | 1) => void;
  onLoadToolPayloads?: () => void;
  toolPayloadsLoading?: boolean;
  toolPayloadsLoaded?: boolean;
  onSampleSources?: () => void;
  sourceSampling?: boolean;
  sourceSample?: AgentSourceSampleResponse | null;
};

const focusSection = (target: QualityIssue['target']) => {
  if (!target) return;
  const elementId = target === 'failed-tool' ? 'run-failed-tool' : `run-${target}`;
  document.getElementById(elementId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

export function RunDetailContent({
  detail,
  onFeedback,
  onLoadToolPayloads,
  toolPayloadsLoading = false,
  toolPayloadsLoaded = false,
  onSampleSources,
  sourceSampling = false,
  sourceSample = null,
}: RunDetailContentProps) {
  const run = detail.snapshot.run ?? {};
  const projection = detail.snapshot.qualityProjection ?? {};
  const selectedAgentMode = text(projection.agentMode)
    || (projection.goal ? 'goal' : projection.team ? 'team' : projection.planning ? 'plan' : '');
  const resolvedAgentMode = text(projection.resolvedAgentMode)
    || (projection.goal ? 'goal' : projection.team ? text(projection.team.resolvedAgentMode) : '');
  const modeLabel = selectedAgentMode
    ? selectedAgentMode === 'auto' && resolvedAgentMode
      ? `Auto → ${agentModeLabel(resolvedAgentMode)}`
      : agentModeLabel(resolvedAgentMode || selectedAgentMode)
    : '';
  const behaviorAudit = detail.snapshot.behaviorAudit;
  const toolResults = projection.toolResults ?? [];
  const executionTrace = record(field(detail.snapshot.trace, ['executionTrace', 'execution_trace']));
  const goalRuntimeErrors = projection.runtimeErrors
    ?? arrayFrom(field(executionTrace, ['runtimeErrors', 'runtime_errors'])).map(record);
  const evidence = projection.evidence ?? [];
  const claimEvidence = projection.claimEvidence ?? [];
  const steps = detail.snapshot.steps ?? [];
  const toolNames = uniqueStrings(toolResults.map((item) => text(item.toolName)).filter(Boolean));
  const sourceNames = uniqueStrings(evidence.flatMap((item) => stringList(item.sourceRefs)));
  const sourceLabels = sourceNames.length > 0 ? uniqueStrings(sourceNames.map(sourceLabel)) : toolNames;
  const feedbackRating = detail.snapshot.feedback?.rating;
  const failedToolResults = toolResults.filter((result) => (
    toolOutcomeFor(text(result.toolName), result).executionStatus !== 'completed'
  ));
  const firstFailedToolIndex = toolResults.findIndex((result) => (
    toolOutcomeFor(text(result.toolName), result).executionStatus !== 'completed'
  ));
  const failedSteps = steps.filter((step) => (
    ['failed', 'blocked', 'cancelled'].includes(text(step.status))
    || (!text(step.status) && (Boolean(errorCodeFrom(step)) || errorDetailsFrom(step).length > 0))
  ));
  const callIds = (item: Record<string, unknown>) => uniqueStrings([
    text(field(item, ['actionId', 'action_id'])),
    text(field(item, ['modelToolCallId', 'model_tool_call_id'])),
    text(field(item, ['stepId', 'step_id'])),
    text(field(item, ['toolCallId', 'tool_call_id'])),
  ].filter(Boolean));
  const observedCallIds = new Set(toolResults.flatMap(callIds));
  const unobservedFailedSteps = failedSteps.filter((step) => !callIds(step).some((id) => observedCallIds.has(id)));
  const representedFailureIds = new Set([
    ...failedToolResults,
    ...unobservedFailedSteps,
  ].flatMap(callIds));
  const seenGoalRuntimeIds = new Set<string>();
  const unrepresentedGoalErrors = goalRuntimeErrors.filter((error) => {
    const failureKind = text(field(error, ['failureKind', 'failure_kind']));
    if (!['tool', 'tool_validation', 'model_output'].includes(failureKind)) return false;
    const actionId = text(field(error, ['actionId', 'action_id']));
    const errorId = text(field(error, ['errorId', 'error_id']));
    const identity = actionId || errorId;
    if (identity && (representedFailureIds.has(identity) || seenGoalRuntimeIds.has(identity))) return false;
    if (identity) seenGoalRuntimeIds.add(identity);
    return true;
  });
  const failedCallCount = failedToolResults.length + unobservedFailedSteps.length + unrepresentedGoalErrors.length;
  const blockedRetryCount = [...failedToolResults, ...unobservedFailedSteps]
    .filter((item) => errorCodeFrom(item) === 'repeated_failed_source').length;
  const runErrorCodes = uniqueStrings([
    errorCodeFrom(run),
    errorCodeFrom(detail.snapshot.trace),
  ].filter(Boolean));
  const runErrorDetails = uniqueStrings([
    ...errorDetailsFrom(run),
    ...errorDetailsFrom(detail.snapshot.trace),
  ]);
  const toolErrorCodes = uniqueStrings([
    ...failedSteps.map(errorCodeFrom),
    ...failedToolResults.map(errorCodeFrom),
    ...unrepresentedGoalErrors.map(errorCodeFrom),
  ].filter(Boolean));
  const toolErrorDetails = uniqueStrings([
    ...failedSteps.flatMap((step) => {
      const label = text(step.toolName) || text(step.tool_name) || text(step.stepId) || text(step.step_id);
      return errorDetailsFrom(step).map((error) => label ? `${label}: ${error}` : error);
    }),
    ...failedToolResults.flatMap((result) => {
      const label = text(result.toolName) || '工具调用';
      return errorDetailsFrom(result).map((error) => `${label}: ${error}`);
    }),
    ...unrepresentedGoalErrors.flatMap((error) => {
      const label = text(field(error, ['toolName', 'tool_name']))
        || text(field(error, ['phase']))
        || 'Goal 动作';
      return errorDetailsFrom(error).map((message) => `${label}: ${message}`);
    }),
  ]);
  const runStatus = text(run.status);
  const hasRunFailure = ['partial', 'failed', 'blocked', 'cancelled'].includes(runStatus);
  const toolFailureNeedsAttention = !behaviorAudit || behaviorAudit.findings.some((finding) => (
    finding.category === 'execution' && finding.disposition !== 'advisory'
  ));
  const durationMs = text(run.startedAt) && text(run.finishedAt)
    ? new Date(text(run.finishedAt)).getTime() - new Date(text(run.startedAt)).getTime()
    : null;
  const qualityIssues = buildQualityIssues(detail.score, failedToolResults, failedSteps, behaviorAudit, runStatus);
  const incompleteGoalResult = Boolean(
    projection.goal
    && text(projection.goal.status) !== 'completed'
    && text(run.finalText),
  );
  const behaviorReviewCount = behaviorAudit?.actionRequiredCount
    ?? (behaviorAudit?.dangerCount ?? 0) + (behaviorAudit?.warningCount ?? 0);
  const behaviorAdvisoryCount = behaviorAudit?.advisoryCount ?? behaviorAudit?.infoCount ?? 0;
  const answerStatusTone = !detail.score.passed || behaviorReviewCount > 0
    ? 'text-warning'
    : behaviorAudit?.status === 'info'
      ? 'text-cyan'
      : 'text-success';
  const [expandedActionId, setExpandedActionId] = useState<string | null>(null);

  return (
    <div className="max-h-[calc(100vh-9rem)] space-y-3 overflow-y-auto pr-1">
      <Card padding="none" className="rounded-xl p-3">
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-1.5">
              <h2 className="text-sm font-semibold text-foreground">回答结果</h2>
              <Badge variant={statusVariant(text(run.status))}>{STATUS_LABELS[text(run.status)] ?? text(run.status)}</Badge>
              {modeLabel ? <Badge variant="info">{modeLabel}</Badge> : null}
            </div>
            <p className="mt-1.5 text-xs text-secondary-text">生成于 {formatDateTime(text(run.createdAt))}</p>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-secondary-text">这次结果有帮助吗？</span>
            <button type="button" onClick={() => onFeedback(1)} className={cn('rounded-md border p-1.5 transition', feedbackRating === 1 ? 'border-success/40 bg-success/10 text-success' : 'border-border text-secondary-text hover:text-success')} aria-label="有帮助"><ThumbsUp className="size-3.5" /></button>
            <button type="button" onClick={() => onFeedback(-1)} className={cn('rounded-md border p-1.5 transition', feedbackRating === -1 ? 'border-danger/40 bg-danger/10 text-danger' : 'border-border text-secondary-text hover:text-danger')} aria-label="没帮助"><ThumbsDown className="size-3.5" /></button>
          </div>
        </div>
        <div className="mt-3 rounded-lg border border-border/70 bg-muted/35 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-semibold text-foreground">{incompleteGoalResult ? '阶段性结果（Goal 未完成）' : '助手给出的结论'}</p>
            <span className={cn('text-[11px] font-medium', answerStatusTone)}>
              {behaviorAudit?.status === 'danger'
                ? `执行诊断发现 ${behaviorReviewCount} 个需要处理的问题`
                : behaviorAudit?.status === 'warning'
                  ? `有 ${behaviorReviewCount} 个需要处理的核对问题`
                  : behaviorAudit?.status === 'info'
                    ? `运行完成，有 ${behaviorAdvisoryCount} 个观察提示`
                  : detail.score.passed
                    ? '回答与引用规则核对通过'
                    : `有 ${detail.score.violations.length} 个待核对问题`}
            </span>
          </div>
          <div className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap text-xs leading-6 text-foreground/85">{text(run.finalText) || '尚未生成最终回答。'}</div>
        </div>
        {hasRunFailure ? <div className={cn('mt-3 rounded-lg border p-3', runStatus === 'failed' ? 'border-danger/30 bg-danger/5' : 'border-warning/30 bg-warning/5')}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className={cn('text-xs font-semibold', runStatus === 'failed' ? 'text-danger' : 'text-warning')}>
                {runStatus === 'failed' ? '本次运行失败' : runStatus === 'cancelled' ? '本次运行已取消' : runStatus === 'blocked' ? '本次运行受阻' : '本次运行未完整完成'}
              </p>
              <p className="mt-0.5 text-[11px] text-foreground/70">
                可展开查看本次运行的终止原因，工具调用情况见下方执行诊断。
              </p>
            </div>
            <Badge variant={statusVariant(runStatus)}>{STATUS_LABELS[runStatus] ?? '异常'}</Badge>
          </div>
          <ErrorDetails
            title="查看运行错误详情"
            errorCode={runErrorCodes.join('、')}
            details={runErrorDetails}
            defaultOpen={runStatus === 'failed'}
            fallback="运行记录中没有返回具体错误文本，请结合调用编号和服务端日志继续排查。"
          />
        </div> : null}
        {failedCallCount > 0 ? <div className={cn('mt-3 rounded-lg border p-3', toolFailureNeedsAttention ? 'border-warning/30 bg-warning/5' : 'border-cyan/30 bg-cyan/5')}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-xs font-semibold text-foreground">
                {blockedRetryCount > 0
                  ? `${failedCallCount - blockedRetryCount} 次工具失败，${blockedRetryCount} 次重复请求已拦截`
                  : projection.goal
                    ? `有 ${failedCallCount} 次工具或动作尝试失败`
                    : runStatus === 'completed' ? `已完成，有 ${failedCallCount} 次工具调用失败` : `有 ${failedCallCount} 次工具调用失败`}
              </p>
              <p className="mt-0.5 text-[11px] text-foreground/70">失败尝试及恢复情况保留在执行诊断中，可展开查看具体原因。</p>
            </div>
            <Badge variant={toolFailureNeedsAttention ? 'warning' : 'info'}>{failedCallCount} 次{blockedRetryCount > 0 ? '失败或拦截' : '失败尝试'}</Badge>
          </div>
          <ErrorDetails title={projection.goal ? '查看工具或动作错误' : '查看工具调用错误'} errorCode={toolErrorCodes.join('、')} details={toolErrorDetails} fallback="请查看对应工具的调用记录。" />
        </div> : null}
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric icon={<Clock3 className="size-3.5 text-cyan" />} label="耗时" value={formatDuration(durationMs)} />
          <Metric icon={<ListChecks className="size-3.5 text-purple" />} label="资料返回" value={String(toolResults.length)} />
          <Metric icon={<Database className="size-3.5 text-emerald-600" />} label="资料入口" value={String(toolNames.length)} />
          <Metric icon={<Activity className="size-3.5 text-warning" />} label="规则核对分" value={percent(detail.score.totalScore)} />
        </div>
        <p className="mt-2 text-[11px] leading-5 text-secondary-text">核对分反映回答完整性、引用关联和运行规则的检查结果，不代表事实准确率。工具异常与恢复情况请看执行诊断。</p>
      </Card>

      <BehaviorAuditCard audit={behaviorAudit} onSampleSources={onSampleSources} sourceSampling={sourceSampling} sourceSample={sourceSample} />

      <PlanningAuditCard planning={projection.planning} />

      <GoalAuditCard goal={projection.goal} />

      <TeamAuditCard team={projection.team} />

      <ClaimEvidenceCard claims={claimEvidence} />

      <div id="run-evidence" className="scroll-mt-3">
        <Card padding="none" className="rounded-xl p-3" title="参考资料" subtitle="回答依据">
          <p className="text-xs text-secondary-text">
            本次回答关联 {sourceLabels.length} 个来源{sourceNames.length > sourceLabels.length ? `（${sourceNames.length} 条原始引用）` : ''}，整理成 {evidence.length} 条可核对证据。
          </p>
          {sourceLabels.length > 0 ? <div className="mt-2 flex flex-wrap gap-1">{sourceLabels.map((source) => <span key={source} className="rounded-md bg-cyan/8 px-2 py-1 text-[11px] text-cyan" title={source}>{source}</span>)}</div> : null}
          <div className="mt-2 divide-y divide-border/70 rounded-lg border border-border/70">
            {evidence.map((item, index) => <EvidenceRow key={text(item.evidenceId) || text(item.id) || index} item={item} index={index} />)}
            {evidence.length === 0 ? <p className="px-2.5 py-2 text-xs text-secondary-text">本次回答没有可展示的资料记录。</p> : null}
          </div>
        </Card>
      </div>

      <Card padding="none" className="rounded-xl p-3" title="需要处理的问题" subtitle="汇总未解决的工具调用与回答核对问题">
        {qualityIssues.length > 0 ? <div className="space-y-1.5">
          {qualityIssues.map((issue) => <div key={issue.key} className={cn('rounded-lg border px-3 py-2', issue.tone === 'danger' ? 'border-danger/20 bg-danger/5' : 'border-warning/20 bg-warning/8')}>
            <div className="flex items-start justify-between gap-2">
              <div className="flex min-w-0 items-start gap-2">
                <TriangleAlert className={cn('mt-0.5 size-3.5 shrink-0', issue.tone === 'danger' ? 'text-danger' : 'text-warning')} />
                <div className="min-w-0">
                  <p className={cn('text-xs font-medium', issue.tone === 'danger' ? 'text-danger' : 'text-warning')}>{issue.title}</p>
                  <p className="mt-0.5 text-[11px] leading-5 text-foreground/75">{issue.detail}</p>
                </div>
              </div>
              {issue.target ? <button type="button" onClick={() => focusSection(issue.target)} className="shrink-0 rounded-md border border-border/70 bg-card px-2 py-1 text-[10px] text-secondary-text transition hover:text-foreground">{issue.actionLabel}</button> : null}
            </div>
          </div>)}
        </div> : <div className="flex items-center gap-2 rounded-lg bg-success/8 px-3 py-2 text-xs text-success"><CheckCircle2 className="size-3.5" />这次没有需要用户继续处理的核对问题。</div>}
      </Card>

      <div id="run-tools" className="scroll-mt-3">
        <Card padding="none" className="rounded-xl p-3" title="资料获取过程" subtitle="本次回答实际使用的资料入口">
        <div className="space-y-2">
          {toolResults.map((result, index) => {
            const actionId = text(result.actionId) || text(result.toolCallId);
            const actionEvidence = evidence.filter((item) => text(item.actionId) === text(result.actionId));
            const actionSources = uniqueStrings(actionEvidence.flatMap((item) => stringList(item.sourceRefs)).map(sourceLabel));
            const toolErrorDetails = errorDetailsFrom(result);
            const toolErrorCode = errorCodeFrom(result);
            const step = findStepForTool(steps, result, actionId);
            const toolName = text(result.toolName) || '未指定工具';
            const expanded = expandedActionId === actionId;
            const hasStepPayload = Boolean(step && (hasValue(step.arguments) || hasValue(step.result)));
            const canLoadPayloads = Boolean(onLoadToolPayloads) && !toolPayloadsLoaded && !hasStepPayload;
            const outcome = toolOutcomeFor(toolName, result);
            return <div id={index === firstFailedToolIndex ? 'run-failed-tool' : undefined} key={actionId || index} className="rounded-lg border border-border/70 p-2.5 scroll-mt-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-xs font-medium">{toolName}</p>
                  <p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={actionId}>调用编号 {actionId}</p>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  <Badge variant={toolOutcomeVariant(outcome)}>{toolOutcomeLabel(outcome)}</Badge>
                </div>
              </div>
              <p className="mt-2 line-clamp-2 text-[11px] text-foreground/75">{actionSources.join('、') || '来源未标注'} · {actionEvidence.length} 条证据</p>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-secondary-text">
                <span>类型：{text(result.effect) === 'side_effect' ? '外部操作' : '读取资料'}</span>
                <span>数据时间：{displayDataTime(result.dataTime || actionEvidence[0]?.dataTime)}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => {
                    setExpandedActionId((current) => current === actionId ? null : actionId);
                    if (canLoadPayloads) onLoadToolPayloads?.();
                  }}
                  className="rounded-md border border-cyan/30 bg-cyan/8 px-2 py-1 text-[11px] font-medium text-cyan transition hover:bg-cyan/15"
                >
                  {expanded ? '收起请求与返回' : canLoadPayloads ? '加载请求与返回' : '查看请求与返回'}
                </button>
                {canLoadPayloads && toolPayloadsLoading ? <span className="inline-flex items-center gap-1 text-[10px] text-secondary-text"><LoaderCircle className="size-3 animate-spin" />正在加载原始记录…</span> : null}
              </div>
              {expanded ? <ToolObservationDetails
                result={result}
                step={step}
                actionSources={actionSources}
                payloadsLoaded={toolPayloadsLoaded || hasStepPayload}
                payloadsLoading={toolPayloadsLoading}
                onLoadPayloads={onLoadToolPayloads}
              /> : null}
              {outcome.executionStatus !== 'completed' ? <ErrorDetails title={toolErrorDetails.length > 0 || toolErrorCode ? '查看错误详情' : '查看失败记录'} errorCode={toolErrorCode} details={toolErrorDetails} fallback="该工具调用标记为失败，但运行记录没有返回具体错误文本。" /> : null}
            </div>;
          })}
          {toolResults.length === 0 ? <p className="text-xs text-secondary-text">该问题没有调用工具。</p> : null}
        </div>
        </Card>
      </div>
    </div>
  );
}
