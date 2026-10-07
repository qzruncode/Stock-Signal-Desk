import type { AgentBehaviorAudit, AgentRunDetail } from '../../api/runExplorer';
import { formatDateTime } from '../../utils/format';

export const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', running: '运行中', recovering: '恢复中', interrupted: '等待审批',
  completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消', blocked: '已阻止',
  succeeded: '已完成',
};

export const statusVariant = (status: string) => {
  if (status === 'completed' || status === 'succeeded') return 'success' as const;
  if (status === 'running' || status === 'recovering' || status === 'queued') return 'info' as const;
  if (status === 'partial' || status === 'blocked' || status === 'interrupted') return 'warning' as const;
  return 'danger' as const;
};

export const text = (value: unknown) => (typeof value === 'string' ? value : value == null ? '' : String(value));
export const percent = (value?: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`);
export const formatDuration = (durationMs?: number | null) => {
  if (durationMs == null) return '—';
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  return `${Math.floor(durationMs / 60_000)} 分 ${Math.round((durationMs % 60_000) / 1000)} 秒`;
};
export const stringList = (value: unknown) => (Array.isArray(value) ? value.map(text).filter(Boolean) : []);
export const uniqueStrings = (values: string[]) => Array.from(new Set(values));
export const record = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
);
const errorString = (value: unknown) => (typeof value === 'string' ? value.trim() : '');
export const errorStringList = (value: unknown): string[] => (
  Array.isArray(value)
    ? value.flatMap(errorStringList)
    : errorString(value)
      ? [errorString(value)]
      : []
);
export const errorDetailsFrom = (value: unknown) => {
  const item = record(value);
  const nestedResult = record(item.result);
  const runtimeErrors = [
    ...sourceAttemptList(item.runtimeErrors ?? item.runtime_errors),
    record(item.runtimeError ?? item.runtime_error),
    ...sourceAttemptList(nestedResult.runtimeErrors ?? nestedResult.runtime_errors),
    record(nestedResult.runtimeError ?? nestedResult.runtime_error),
  ].filter((entry) => Object.keys(entry).length > 0);
  const runtimeDetails = runtimeErrors.flatMap((entry) => {
    const code = errorString(entry.errorCode) || errorString(entry.error_code);
    const type = errorString(entry.exceptionType) || errorString(entry.exception_type);
    const message = errorString(entry.message) || errorString(entry.error);
    const fallback = errorString(entry.fallbackStatus) || errorString(entry.fallback_status);
    const retryable = entry.retryable === true ? '可重试' : '';
    return [
      code ? `运行时错误码：${code}` : '',
      type ? `异常类型：${type}` : '',
      message ? `运行时原因：${message}` : '',
      fallback ? `网页兜底：${fallback}` : '',
      retryable,
    ].filter(Boolean);
  });
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
    ...runtimeDetails,
  ].filter(Boolean));
};
export const errorCodeFrom = (value: unknown) => {
  const item = record(value);
  const nestedResult = record(item.result);
  const runtimeError = [
    record(item.runtimeError ?? item.runtime_error),
    ...sourceAttemptList(item.runtimeErrors ?? item.runtime_errors),
  ].find((entry) => Object.keys(entry).length > 0) ?? {};
  return errorString(item.errorCode)
    || errorString(item.error_code)
    || errorString(nestedResult.errorCode)
    || errorString(nestedResult.error_code)
    || errorString(runtimeError.errorCode)
    || errorString(runtimeError.error_code);
};
export const isHttpUrl = (value: string) => /^https?:\/\//i.test(value);
const REFERENCE_ONLY_TOOLS = new Set(['read_company_research_reports_akshare', 'read_company_news_akshare']);
const CONTENT_READER_TOOLS = new Set(['read_web_source', 'read_text_document', 'read_rss_item', 'read_registered_rss_item']);
export const accessModeFor = (toolName: string, result?: Record<string, unknown>) => {
  const access = record(field(result, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit']));
  const explicit = text(field(access, ['mode', 'accessMode', 'access_mode']));
  if (explicit) return explicit;
  if (REFERENCE_ONLY_TOOLS.has(toolName)) return 'reference_only';
  if (CONTENT_READER_TOOLS.has(toolName)) return 'content_read';
  return 'structured_data';
};

export type ToolOutcome = {
  executionStatus: string;
  accessStatus: string;
  dataStatus: string;
  usable: boolean;
  qualityStatus: string;
};

export const toolOutcomeFor = (toolName: string, result?: Record<string, unknown>): ToolOutcome => {
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

export const toolOutcomeLabel = (outcome: ToolOutcome) => {
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
export const toolOutcomeVariant = (outcome: ToolOutcome) => (
  outcome.executionStatus !== 'completed'
    ? 'danger' as const
    : ['empty', 'stale', 'partial'].includes(outcome.dataStatus)
      ? 'warning' as const
      : ['fallback', 'freshness_unknown'].includes(outcome.dataStatus)
        ? 'info' as const
        : 'success' as const
);
export const sourceLabel = (value: string) => {
  const trimmed = value.trim();
  if (!isHttpUrl(trimmed)) return trimmed.replace(/^www\./i, '');
  try {
    return new URL(trimmed).hostname.replace(/^www\./i, '');
  } catch {
    return trimmed;
  }
};
export const displayDataTime = (value: unknown) => (text(value) ? formatDateTime(text(value)) : '时间未提供');
export const hasValue = (value: unknown) => value !== null && value !== undefined;
export const field = (value: unknown, keys: string[]) => {
  const item = record(value);
  for (const key of keys) {
    if (hasValue(item[key])) return item[key];
  }
  return undefined;
};
export const numberValue = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
};
export const jsonString = (value: unknown) => {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2) ?? text(value);
  } catch {
    return text(value);
  }
};
export const previewJson = (value: unknown, limit = 20_000) => {
  const serialized = jsonString(value);
  return serialized.length > limit
    ? `${serialized.slice(0, limit)}\n…[预览已截断，复制按钮仍保留当前脱敏后的完整内容]`
    : serialized;
};
export const arrayFrom = (value: unknown): unknown[] => {
  if (Array.isArray(value)) return value;
  const item = record(value);
  for (const key of ['data', 'items', 'rows', 'records', 'resultItems', 'result_items']) {
    if (Array.isArray(item[key])) return item[key];
  }
  return [];
};
export const sourceAttemptList = (value: unknown) => (
  Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length > 0) : []
);
export const findStepForTool = (
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

export type QualityIssue = {
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

export function buildQualityIssues(
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
  const hasFailedTools = (failedToolResults.length > 0 || failedSteps.length > 0) && (
    !behaviorAudit || behaviorAudit.findings.some((finding) => (
      finding.code === 'tool_execution_failed' && finding.disposition !== 'advisory'
    ))
  );
  if (
    (behaviorAudit
      ? hasActionableExecutionFinding
      : failedToolResults.length > 0 || failedSteps.length > 0)
    || (execution && execution.score < 1)
  ) {
    const failedTools = hasFailedTools ? uniqueStrings([
      ...failedToolResults.map((item) => text(item.toolName)),
      ...failedSteps.map((item) => text(item.toolName) || text(item.tool_name)),
    ].filter(Boolean)) : [];
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
