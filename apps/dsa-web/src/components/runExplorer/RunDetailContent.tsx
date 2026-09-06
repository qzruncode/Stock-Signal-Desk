import { useState } from 'react';
import type React from 'react';
import { Activity, CheckCircle2, Clock3, Copy, Database, Link2, ListChecks, LoaderCircle, ShieldCheck, ThumbsDown, ThumbsUp, TriangleAlert } from 'lucide-react';
import type { AgentBehaviorAudit, AgentRunDetail, AgentSourceSampleResponse } from '../../api/runExplorer';
import { Badge, Card } from '../common';
import { cn } from '../../utils/cn';
import { formatDateTime } from '../../utils/format';

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

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', running: '运行中', recovering: '恢复中', interrupted: '等待审批',
  completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消', blocked: '已阻止',
  succeeded: '已完成',
};

const statusVariant = (status: string) => {
  if (status === 'completed' || status === 'succeeded') return 'success' as const;
  if (status === 'running' || status === 'recovering' || status === 'queued') return 'info' as const;
  if (status === 'partial' || status === 'blocked' || status === 'interrupted') return 'warning' as const;
  return 'danger' as const;
};

const text = (value: unknown) => (typeof value === 'string' ? value : value == null ? '' : String(value));
const percent = (value?: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`);
const formatDuration = (durationMs?: number | null) => {
  if (durationMs == null) return '—';
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  return `${Math.floor(durationMs / 60_000)} 分 ${Math.round((durationMs % 60_000) / 1000)} 秒`;
};
const stringList = (value: unknown) => (Array.isArray(value) ? value.map(text).filter(Boolean) : []);
const uniqueStrings = (values: string[]) => Array.from(new Set(values));
const record = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
);
const errorString = (value: unknown) => (typeof value === 'string' ? value.trim() : '');
const errorStringList = (value: unknown): string[] => (
  Array.isArray(value)
    ? value.flatMap(errorStringList)
    : errorString(value)
      ? [errorString(value)]
      : []
);
const errorDetailsFrom = (value: unknown) => {
  const item = record(value);
  const nestedResult = record(item.result);
  return uniqueStrings([
    ...errorStringList(item.errors),
    ...errorStringList(item.errorMessages),
    errorString(item.errorDetail),
    errorString(item.error_detail),
    errorString(item.error),
    errorString(item.message),
    ...errorStringList(nestedResult.errors),
    ...errorStringList(nestedResult.errorMessages),
    errorString(nestedResult.errorDetail),
    errorString(nestedResult.error_detail),
    errorString(nestedResult.error),
    errorString(nestedResult.message),
  ].filter(Boolean));
};
const errorCodeFrom = (value: unknown) => {
  const item = record(value);
  const nestedResult = record(item.result);
  return errorString(item.errorCode)
    || errorString(item.error_code)
    || errorString(nestedResult.errorCode)
    || errorString(nestedResult.error_code);
};
const isHttpUrl = (value: string) => /^https?:\/\//i.test(value);
const REFERENCE_ONLY_TOOLS = new Set(['read_company_research_reports_akshare', 'read_company_news_akshare']);
const CONTENT_READER_TOOLS = new Set(['read_web_source', 'read_text_document', 'read_financial_article', 'read_rss_item', 'read_registered_rss_item']);
const accessModeFor = (toolName: string, result?: Record<string, unknown>) => {
  const access = record(field(result, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit']));
  const explicit = text(field(access, ['mode', 'accessMode', 'access_mode']));
  if (explicit) return explicit;
  if (REFERENCE_ONLY_TOOLS.has(toolName)) return 'reference_only';
  if (CONTENT_READER_TOOLS.has(toolName)) return 'content_read';
  return 'structured_data';
};
type ToolOutcome = {
  executionStatus: string;
  accessStatus: string;
  dataStatus: string;
  usable: boolean;
  qualityStatus: string;
};
const toolOutcomeFor = (toolName: string, result?: Record<string, unknown>): ToolOutcome => {
  const explicit = record(result?.outcome);
  if (text(explicit.executionStatus) || text(explicit.dataStatus) || text(explicit.accessStatus)) {
    return {
      executionStatus: text(explicit.executionStatus) || (result?.success === true ? 'completed' : 'failed'),
      accessStatus: text(explicit.accessStatus) || accessModeFor(toolName, result),
      dataStatus: text(explicit.dataStatus) || 'usable',
      usable: explicit.usable === true,
      qualityStatus: text(explicit.qualityStatus) || 'clear',
    };
  }
  const success = result?.success === true;
  const accessMode = accessModeFor(toolName, result);
  const access = record(field(result, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit']));
  const contentExtracted = field(access, ['contentExtracted', 'content_extracted']) === true;
  const resultCount = numberValue(field(result, ['resultCount', 'result_count', 'count', 'itemCount', 'item_count']));
  const dataStatus = !success
    ? 'error'
    : resultCount === 0 || (accessMode === 'content_read' && !contentExtracted)
      ? 'empty'
      : field(result, ['isStale', 'is_stale']) === true
        ? 'stale'
        : field(result, ['partial', 'partialResult', 'partial_result']) === true
          ? 'partial'
          : field(result, ['fallbackUsed', 'fallback_used']) === true
            ? 'fallback'
            : field(result, ['freshnessUnknown', 'freshness_unknown']) === true
              ? 'freshness_unknown'
              : 'usable';
  const accessStatus = !success && accessMode === 'content_read'
    ? 'content_unavailable'
    : contentExtracted
      ? 'content_extracted'
      : accessMode;
  return {
    executionStatus: success ? 'completed' : 'failed',
    accessStatus,
    dataStatus,
    usable: success && dataStatus !== 'empty',
    qualityStatus: 'unknown',
  };
};
const toolOutcomeLabel = (outcome: ToolOutcome) => {
  if (outcome.executionStatus !== 'completed') return '调用失败';
  const dataLabels: Record<string, string> = {
    empty: '空结果',
    stale: '数据过期',
    partial: '返回不完整',
    fallback: '已降级',
    freshness_unknown: '数据时间未知',
  };
  if (dataLabels[outcome.dataStatus]) return `调用完成 · ${dataLabels[outcome.dataStatus]}`;
  const accessLabels: Record<string, string> = {
    content_extracted: '正文已提取',
    content_unavailable: '正文不可用',
    content_read: '正文已读取',
    reference_only: '来源索引',
    structured_data: '结构化数据',
  };
  return `调用完成 · ${accessLabels[outcome.accessStatus] ?? '已返回'}`;
};
const toolOutcomeVariant = (outcome: ToolOutcome) => (
  outcome.executionStatus !== 'completed'
    ? 'danger' as const
    : ['empty', 'stale', 'partial'].includes(outcome.dataStatus)
      ? 'warning' as const
      : ['fallback', 'freshness_unknown'].includes(outcome.dataStatus)
        ? 'info' as const
        : 'success' as const
);
const sourceLabel = (value: string) => {
  const trimmed = value.trim();
  if (!isHttpUrl(trimmed)) return trimmed.replace(/^www\./i, '');
  try {
    return new URL(trimmed).hostname.replace(/^www\./i, '');
  } catch {
    return trimmed;
  }
};
const displayDataTime = (value: unknown) => (text(value) ? formatDateTime(text(value)) : '时间未提供');
const hasValue = (value: unknown) => value !== null && value !== undefined;
const field = (value: unknown, keys: string[]) => {
  const item = record(value);
  for (const key of keys) {
    if (hasValue(item[key])) return item[key];
  }
  return undefined;
};
const numberValue = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
};
const jsonString = (value: unknown) => {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2) ?? text(value);
  } catch {
    return text(value);
  }
};
const previewJson = (value: unknown, limit = 20_000) => {
  const serialized = jsonString(value);
  return serialized.length > limit
    ? `${serialized.slice(0, limit)}\n…[预览已截断，复制按钮仍保留当前脱敏后的完整内容]`
    : serialized;
};
const arrayFrom = (value: unknown): unknown[] => {
  if (Array.isArray(value)) return value;
  const item = record(value);
  for (const key of ['data', 'items', 'rows', 'records', 'resultItems', 'result_items']) {
    if (Array.isArray(item[key])) return item[key];
  }
  return [];
};
const sourceAttemptList = (value: unknown) => (
  Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length > 0) : []
);
const findStepForTool = (
  steps: Array<Record<string, unknown>>,
  result: Record<string, unknown>,
  actionId: string,
) => {
  const toolCallId = text(result.toolCallId) || text(result.tool_call_id);
  const exact = steps.find((step) => {
    const ids = [
      text(step.stepId), text(step.step_id), text(step.idempotencyKey), text(step.idempotency_key),
    ].filter(Boolean);
    return ids.includes(actionId) || (toolCallId && ids.includes(toolCallId));
  });
  if (exact) return exact;
  const toolName = text(result.toolName) || text(result.tool_name);
  const sameTool = steps.filter((step) => text(step.toolName) === toolName || text(step.tool_name) === toolName);
  return sameTool.length === 1 ? sameTool[0] : undefined;
};
const violationLabel = (code: string) => ({
  execution_contract_failed: '部分执行步骤没有完整结束',
  evidence_link_contract_failed: '部分结论没有完成逐条资料对应',
  answer_contract_failed: '回答内容没有完全满足要求',
  control_loop_contract_failed: '分析过程存在异常或缺少步骤',
  budget_contract_failed: '本次分析触及运行资源上限',
}[code] ?? code.replaceAll('_', ' '));

type QualityIssue = {
  key: string;
  title: string;
  detail: string;
  tone: 'danger' | 'warning';
  target?: 'evidence' | 'failed-tool' | 'tools';
  actionLabel?: string;
};

const dimensionFor = (score: AgentRunDetail['score'], name: string) => (
  score.dimensions[name]
  ?? score.dimensions[name.replace(/_([a-z])/g, (_match, character: string) => character.toUpperCase())]
);

const dimensionDetails = (score: AgentRunDetail['score'], name: string) => (
  dimensionFor(score, name)?.details ?? {}
);

const detailList = (details: Record<string, unknown>, name: string) => (
  stringList(field(details, [name, name.replace(/_([a-z])/g, (_match, character: string) => character.toUpperCase())]))
);

function buildQualityIssues(
  score: AgentRunDetail['score'],
  failedToolResults: Array<Record<string, unknown>>,
  failedSteps: Array<Record<string, unknown>>,
  behaviorAudit?: AgentBehaviorAudit,
  runStatus = '',
): QualityIssue[] {
  const issues: QualityIssue[] = [];
  const execution = dimensionFor(score, 'execution');
  const hasActionableExecutionFinding = behaviorAudit?.findings.some((finding) => (
    finding.category === 'execution' && finding.disposition !== 'advisory'
  ));
  const hasFailedTools = failedToolResults.length > 0 || failedSteps.length > 0;
  if (
    (behaviorAudit
      ? hasActionableExecutionFinding
      : failedToolResults.length > 0 || failedSteps.length > 0)
    || (execution && execution.score < 1)
  ) {
    const failedTools = uniqueStrings([
      ...failedToolResults.map((item) => text(item.toolName)),
      ...failedSteps.map((item) => text(item.toolName) || text(item.tool_name)),
    ].filter(Boolean));
    const status = text(field(dimensionDetails(score, 'execution'), ['status']));
    issues.push({
      key: 'execution',
      title: hasFailedTools ? '工具调用存在未解决的问题' : '运行未完整完成',
      detail: failedTools.length > 0
        ? `${failedTools.slice(0, 3).join('、')}${failedTools.length > 3 ? ` 等 ${failedTools.length} 个入口` : ''}存在失败或未完成记录。`
        : `本次运行状态为“${status || '异常'}”，没有完整结束。`,
      tone: 'danger',
      target: hasFailedTools ? 'failed-tool' : undefined,
      actionLabel: hasFailedTools ? '定位失败工具' : undefined,
    });
  }

  const evidence = dimensionFor(score, 'evidence_links');
  const evidenceDetails = dimensionDetails(score, 'evidence_links');
  const evidenceWithoutSource = detailList(evidenceDetails, 'evidence_without_source');
  const unknownCitations = detailList(evidenceDetails, 'unknown_citations');
  const unmappedCitations = detailList(evidenceDetails, 'unmapped_citations');
  const failedClaimChecks = detailList(evidenceDetails, 'failed_claim_checks');
  if (evidence && evidence.score < 1) {
    const problems = [
      evidenceWithoutSource.length > 0 ? `${evidenceWithoutSource.length} 条证据缺少来源` : '',
      unknownCitations.length > 0 ? `${unknownCitations.length} 个引用找不到对应证据` : '',
      unmappedCitations.length > 0 ? `${unmappedCitations.length} 个引用没有映射到证据台账` : '',
      failedClaimChecks.length > 0 ? `${failedClaimChecks.length} 个结论检查未通过` : '',
    ].filter(Boolean);
    issues.push({
      key: 'evidence',
      title: '部分结论还不能直接核对',
      detail: problems.join('；') || `资料对应度为 ${percent(evidence.score)}，建议检查引用和来源。`,
      tone: 'warning',
      target: 'evidence',
      actionLabel: '查看参考资料',
    });
  }

  const answer = dimensionFor(score, 'answer_contract');
  const answerDetails = dimensionDetails(score, 'answer_contract');
  const missingTerms = detailList(answerDetails, 'missing_required_terms');
  const forbiddenTerms = detailList(answerDetails, 'present_forbidden_terms');
  const terminalFailureWithoutAnswer = ['partial', 'failed', 'blocked', 'cancelled'].includes(runStatus)
    && field(answerDetails, ['has_answer', 'hasAnswer']) !== true
    && !text(field(answerDetails, ['answer', 'final_text', 'finalText']));
  if (answer && answer.score < 1 && !terminalFailureWithoutAnswer) {
    const problems = [
      missingTerms.length > 0 ? `缺少：${missingTerms.join('、')}` : '',
      forbiddenTerms.length > 0 ? `出现不应出现的内容：${forbiddenTerms.join('、')}` : '',
    ].filter(Boolean);
    issues.push({
      key: 'answer',
      title: '回答内容还有待补全',
      detail: problems.join('；') || `回答完整度为 ${percent(answer.score)}。`,
      tone: 'warning',
    });
  }

  const budget = dimensionFor(score, 'budget');
  const budgetDetails = dimensionDetails(score, 'budget');
  const exceeded = record(field(budgetDetails, ['exceeded']));
  if (budget && budget.score < 1) {
    const exceededNames = Object.keys(exceeded);
    issues.push({
      key: 'budget',
      title: '本次运行触及资源限制',
      detail: exceededNames.length > 0
        ? `超出：${exceededNames.join('、')}`
        : text(field(budgetDetails, ['work_budget_detail'])) || '运行资源已用尽，结果可能不完整。',
      tone: 'warning',
    });
  }

  if (issues.length === 0 && score.violations.length > 0) {
    score.violations.forEach((violation, index) => issues.push({
      key: `${violation.code}-${index}`,
      title: violationLabel(violation.code),
      detail: '系统发现需要进一步核对的项目。',
      tone: 'warning',
    }));
  }
  return issues;
}

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
  const behaviorAudit = detail.snapshot.behaviorAudit;
  const toolResults = projection.toolResults ?? [];
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
  const failedCallCount = failedToolResults.length + unobservedFailedSteps.length;
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
            <p className="text-xs font-semibold text-foreground">助手给出的结论</p>
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
                {runStatus === 'completed' ? `已完成，有 ${failedCallCount} 次工具调用失败` : `有 ${failedCallCount} 次工具调用失败`}
              </p>
              <p className="mt-0.5 text-[11px] text-foreground/70">失败尝试及恢复情况保留在执行诊断中，可展开查看调用错误。</p>
            </div>
            <Badge variant={toolFailureNeedsAttention ? 'warning' : 'info'}>{failedCallCount} 次失败尝试</Badge>
          </div>
          <ErrorDetails title="查看工具调用错误" errorCode={toolErrorCodes.join('、')} details={toolErrorDetails} fallback="请查看对应工具的调用记录。" />
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

function ToolObservationDetails({
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

function ErrorDetails({
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

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="rounded-lg bg-muted/60 p-2.5">{icon}<p className="mt-1 text-[11px] text-secondary-text">{label}</p><p className="mt-0.5 text-sm font-medium">{value}</p></div>;
}

function BehaviorAuditCard({
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

function ClaimEvidenceCard({ claims }: { claims: Array<Record<string, unknown>> }) {
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

function EvidenceRow({ item, index }: { item: Record<string, unknown>; index: number }) {
  const refs = stringList(item.sourceRefs);
  const sources = uniqueStrings(refs.map(sourceLabel));
  const urls = refs.filter(isHttpUrl);
  const evidenceId = text(item.evidenceId) || text(item.id) || `证据 ${index + 1}`;
  return <div className="px-2.5 py-2"><div className="flex items-center justify-between gap-2"><span className="line-clamp-2 text-xs font-medium text-foreground" title={refs.join('、')}>{sources.join('、') || text(item.toolName) || '未标注来源'}</span><span className="shrink-0 text-[10px] text-secondary-text">{displayDataTime(item.dataTime)}</span></div><p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={evidenceId}>{evidenceId}</p>{urls.length > 0 ? <details className="mt-1.5 text-[10px] text-secondary-text"><summary className="cursor-pointer select-none hover:text-foreground">查看 {urls.length} 条原始链接</summary><div className="mt-1 space-y-0.5 border-l border-border pl-2">{urls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer" className="block break-all text-cyan hover:underline">{url}</a>)}</div></details> : null}</div>;
}

export default RunDetailContent;
