import type { AgentExecutionTrace } from '../../api/agent';
import { agentStageEvents, type AgentStageEvent } from '../../utils/agentStage';
import { isRecord, recordValue, teamProgressText, type TraceRecord } from './AgentReasoningUtils';
// Team experts are registry-driven.  These strings are only known built-in
// labels, never an authorization or layout union.
export type TeamRole = string;

export type TeamMemberStatus =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'blocked'
  | 'cancelled';

export const terminalStatuses = new Set<TeamMemberStatus>([
  'completed',
  'partial',
  'failed',
  'blocked',
  'cancelled',
]);

export interface TeamPartRecord extends TraceRecord {
  type: string;
}

export interface TeamToolRecord {
  part: TeamPartRecord;
  event?: AgentStageEvent;
  runtimeError?: AgentStageEvent;
}

export interface TeamChartRecord {
  part: TeamPartRecord;
  event?: AgentStageEvent;
}

export interface TeamReviewPhase {
  key: string;
  label: string;
  status: TeamMemberStatus;
  summary: string;
}

export interface TeamReviewReport {
  partId: string;
  title: string;
  blocks: { section: string; content: string }[];
}

export interface TeamFailureModel {
  status: Extract<TeamMemberStatus, 'partial' | 'failed' | 'blocked' | 'cancelled'>;
  errorCode: string;
  detail: string;
  phase: string;
  dispatchStatus: string;
}

export interface TeamMemberModel {
  key: string;
  role: TeamRole;
  label: string;
  taskId: string;
  agentId: string;
  status: TeamMemberStatus;
  progress: string;
  summary: string;
  tools: TeamToolRecord[];
  charts: TeamChartRecord[];
  runtimeErrors: AgentStageEvent[];
  result?: TraceRecord;
}

export type TeamMemberFlowItem = {
  key: string;
  contentIndex: number;
  kind: 'projection' | 'tool' | 'chart';
  projection?: TeamModelProjection;
  tool?: TeamToolRecord;
  chart?: TeamChartRecord;
};

export interface TeamBoardModel {
  isTeam: boolean;
  teamId: string;
  status: TeamMemberStatus;
  members: TeamMemberModel[];
  review: TeamReviewPhase[];
  reviewReports?: TeamReviewReport[];
  coordinatorProgress: string[];
  unassignedTools: TeamToolRecord[];
  unassignedCharts: TeamChartRecord[];
  totalToolCount: number;
  runtimeErrors?: AgentStageEvent[];
  problem: boolean;
  failure: TeamFailureModel | null;
}

export interface TeamWorkerProgressAnchor {
  role: TeamRole;
  taskId: string;
  agentId: string;
  text: string;
}

/**
 * The only Team process text that is allowed to reach the assistant message.
 * Lifecycle summaries remain control-plane data and are intentionally absent
 * from this contract. `partId` is stable across a live update and a replay,
 * while `contentIndex` records the position of the part in the current
 * message stream.
 */
export interface TeamModelProjection {
  partId: string;
  contentIndex: number;
  text: string;
  projectionSource: 'model';
  scope: string;
  namespace: string;
  agentId: string;
  taskId: string;
  phase: string;
  kind: string;
  sequence: number;
}

export const ROLE_ORDER = ['market', 'fundamental', 'news', 'reviewer', 'unknown'];

export const ROLE_LABELS: Record<string, string> = {
  market: '行情分析',
  fundamental: '基本面分析',
  news: '新闻分析',
  reviewer: '综合审查',
  shared: '主体确认',
  unknown: '协作成员',
};

export const isWorkerRole = (role: TeamRole | undefined): boolean => (
  role != null
  && role !== 'reviewer'
  && role !== 'shared'
  && !role.startsWith('review')
);

export const normalizeText = (value: unknown, limit = 2_400): string => (
  typeof value === 'string' ? value.trim().slice(0, limit) : ''
);

export const normalizedRole = (value: unknown): TeamRole | undefined => {
  const candidate = normalizeText(value, 64).toLowerCase();
  return candidate || undefined;
};

export const roleLabel = (role: TeamRole): string => {
  if (ROLE_LABELS[role]) return ROLE_LABELS[role];
  const humanized = role
    .replace(/^team:/, '')
    .replace(/[-_.:]+/g, ' ')
    .trim();
  return humanized ? `${humanized}专家` : ROLE_LABELS.unknown;
};

export const statusValue = (value: unknown): TeamMemberStatus | undefined => {
  const candidate = normalizeText(value, 32).toLowerCase();
  if (candidate === 'succeeded') return 'completed';
  if (candidate === 'running' || candidate === 'started') return 'running';
  if (candidate === 'complete' || candidate === 'completed') return 'completed';
  if (candidate === 'waiting' || candidate === 'requires-action') return 'waiting';
  if (candidate === 'queued' || candidate === 'not_started') return 'queued';
  if (candidate === 'partial' || candidate === 'failed' || candidate === 'blocked' || candidate === 'cancelled') {
    return candidate;
  }
  return undefined;
};

/**
 * A terminal review event must not retain a future-stage progress clause.
 * Older and some provider-specific traces used summaries such as
 * "冲突检测完成，正在进入独立批评复核".  The lifecycle status is already
 * terminal, so keep the completed fact and remove only the contradictory
 * next-stage clause while preserving useful failure/partial details.
 */
export const reviewSummaryForStatus = (
  summary: string,
  status: TeamMemberStatus | undefined,
): string => {
  if (!summary || !status || !terminalStatuses.has(status)) return summary;
  const activeClause = summary.search(/[，,；;]\s*(?=(?:正在|准备|即将|等待|进入))/);
  if (activeClause >= 0) return summary.slice(0, activeClause).trim();
  return /^(?:正在|准备|即将|等待|进入)/.test(summary) ? '' : summary;
};

/**
 * A publish stage can carry the transport status `failed` while its durable
 * Team outcome is `partial` (for example, an evidence-link check failed after
 * usable worker results had already been published).  The durable outcome is
 * stored in the stage details and must win over the transport status when we
 * project the Team header.
 */
export const stageOutcomeStatus = (event: AgentStageEvent | undefined): TeamMemberStatus | undefined => (
  statusValue(recordValue(event?.details, 'status', 'team_status', 'teamStatus'))
);

export const partValue = (part: TeamPartRecord, ...keys: string[]): unknown => (
  recordValue(part, ...keys)
);

export const partToolId = (part: TeamPartRecord): string => normalizeText(
  partValue(part, 'toolCallId', 'tool_call_id'),
  192,
);

export const partToolName = (part: TeamPartRecord): string => normalizeText(
  partValue(part, 'toolName', 'tool_name'),
  160,
);

export const eventIdentityValues = (event: AgentStageEvent): string[] => [
  event.actionId,
  event.toolCallId,
].map((value) => normalizeText(value, 192)).filter(Boolean);

export const partIdentityValues = (part: TeamPartRecord): string[] => [
  partToolId(part),
  normalizeText(partValue(part, 'parentId', 'parent_id'), 192),
].filter(Boolean);

export const identitiesMatch = (left: string, right: string): boolean => (
  left === right
  || left.endsWith(`:${right}`)
  || right.endsWith(`:${left}`)
);

export const eventRole = (event: AgentStageEvent): TeamRole | undefined => {
  const details = event.details || {};
  return normalizedRole(recordValue(details, 'expert_id', 'expertId', 'agent_id', 'agentId'));
};

export const eventTaskId = (event: AgentStageEvent): string => normalizeText(
  recordValue(event.details, 'task_id', 'taskId'),
  96,
);

export const eventAgentId = (event: AgentStageEvent): string => normalizeText(
  recordValue(event.details, 'agent_id', 'agentId'),
  96,
);

export const isToolEvent = (event: AgentStageEvent): boolean => (
  event.stage === 'tool' || event.stage === 'execute'
);

export const findToolEvent = (
  part: TeamPartRecord,
  events: readonly AgentStageEvent[],
): AgentStageEvent | undefined => {
  const identities = partIdentityValues(part);
  if (identities.length === 0) return undefined;
  return events
    .filter((event) => isToolEvent(event) && eventRole(event))
    .filter((event) => eventIdentityValues(event).some((eventIdentity) => (
      identities.some((partIdentity) => identitiesMatch(eventIdentity, partIdentity))
    )))
    .at(-1);
};

export const findChartEvent = (
  part: TeamPartRecord,
  events: readonly AgentStageEvent[],
): AgentStageEvent | undefined => {
  const data = isRecord(part.data) ? part.data : {};
  const identities = [
    data.actionId,
    data.action_id,
    data.toolCallId,
    data.tool_call_id,
  ].map((value) => normalizeText(value, 192)).filter(Boolean);
  if (identities.length === 0) return undefined;
  return events
    .filter((event) => isToolEvent(event) && eventRole(event))
    .filter((event) => eventIdentityValues(event).some((eventIdentity) => (
      identities.some((chartIdentity) => identitiesMatch(eventIdentity, chartIdentity))
    )))
    .at(-1);
};

export const findRuntimeErrorEvent = (
  part: TeamPartRecord,
  events: readonly AgentStageEvent[],
): AgentStageEvent | undefined => {
  const identities = partIdentityValues(part);
  if (identities.length === 0) return undefined;
  return events
    .filter((event) => event.stage === 'runtime_error')
    .filter((event) => eventIdentityValues(event).some((eventIdentity) => (
      identities.some((partIdentity) => identitiesMatch(eventIdentity, partIdentity))
    )))
    .at(-1);
};

export const teamTrace = (trace: AgentExecutionTrace | null | undefined): TraceRecord | undefined => (
  isRecord(trace?.team) ? trace.team : undefined
);

export const recordsFrom = (value: unknown): TraceRecord[] => (
  Array.isArray(value) ? value.filter(isRecord) : []
);

export const taskRecords = (team: TraceRecord | undefined): TraceRecord[] => {
  const plan = isRecord(team?.plan)
    ? team.plan
    : isRecord(team?.collaboration) && isRecord(team.collaboration.plan)
      ? team.collaboration.plan
      : undefined;
  return recordsFrom(plan?.tasks ?? (isRecord(team?.collaboration) ? team.collaboration.tasks : undefined));
};

export const resultRecords = (team: TraceRecord | undefined): TraceRecord[] => {
  const direct = recordsFrom(team?.results);
  if (direct.length > 0) return direct;
  const reports = isRecord(team?.collaboration) ? team.collaboration.reports : undefined;
  return isRecord(reports) ? Object.values(reports).filter(isRecord) : [];
};

export const latestByOrder = <T,>(values: readonly T[]): T | undefined => values.at(-1);

export const stageEventsFromParts = (parts: readonly unknown[]): Record<string, unknown>[] => (
  parts.flatMap((value) => {
    if (!isRecord(value) || value.type !== 'data') return [];
    const name = normalizeText(value.name, 96);
    const data = isRecord(value.data) ? value.data : undefined;
    if (!data || (name !== 'agent-stage' && name !== 'agent_stage'
      && data.event !== 'agent_stage' && data.event !== 'agent_stage_v2')) {
      return [];
    }
    return [data];
  })
);

export const stageEventIdentity = (event: AgentStageEvent): string => [
  event.event,
  event.runId,
  event.collaborationId || '',
  event.stage,
  event.status,
  event.actionId || '',
  event.toolCallId || '',
  event.roundId || '',
  event.occurredAt || '',
  event.summary,
].join('|');

/**
 * Merge durable metadata and live native data parts into one ordered stream.
 *
 * Live Team stage events arrive as ordered ``data`` parts while hydrated
 * conversations receive the same events through ``unstable_data``.  Reading
 * only the latter leaves a stale worker start in the live card; reading both
 * without reconciliation duplicates every event after a refresh.
 */
export const orderedStageEvents = (
  values: readonly unknown[],
): AgentStageEvent[] => {
  const seen = new Set<string>();
  return agentStageEvents(values)
    .filter((event) => {
      const identity = stageEventIdentity(event);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    })
    .map((event, index) => ({ event, index }))
    .sort((left, right) => {
      const leftSequence = left.event.sequence;
      const rightSequence = right.event.sequence;
      if (Number.isFinite(leftSequence) && Number.isFinite(rightSequence)
        && leftSequence !== rightSequence) {
        return Number(leftSequence) - Number(rightSequence);
      }
      const leftTime = left.event.occurredAt ? Date.parse(left.event.occurredAt) : Number.NaN;
      const rightTime = right.event.occurredAt ? Date.parse(right.event.occurredAt) : Number.NaN;
      if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      return left.index - right.index;
    })
    .map(({ event }) => event);
};

export const camelCaseKey = (value: string): string => value.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());

export const reviewPhase = (
  team: TraceRecord | undefined,
  key: string,
  label: string,
  statusKey: string,
  valueKey: string,
  events: readonly AgentStageEvent[],
): TeamReviewPhase | undefined => {
  const rawStatus = recordValue(team, statusKey, camelCaseKey(statusKey));
  const status = statusValue(rawStatus);
  const rawValue = recordValue(team, valueKey, camelCaseKey(valueKey));
  const value = isRecord(rawValue) ? rawValue : undefined;
  const actionSuffix: Record<string, string> = {
    'evidence-merge': 'evidence-merger',
    'review-dispatch': 'review-dispatch',
    conflict: 'conflict-detector',
    critic: 'critic-reviewer',
    'review-gate': 'review-gate',
    'bull-case': 'bull-case-reviewer',
    'bear-case': 'bear-case-reviewer',
    consensus: 'consensus-resolver',
    criteria: 'completion-criteria-validator',
  };
  const teamId = normalizeText(recordValue(team, 'team_id', 'teamId'), 96);
  const reviewEvent = [...events].reverse().find((event) => {
    const eventTeamId = normalizeText(recordValue(event.details, 'team_id', 'teamId'), 96);
    const actionId = normalizeText(event.actionId, 192);
    return Boolean(actionSuffix[key])
      && actionId.endsWith(`:${actionSuffix[key]}`)
      && (!teamId || !eventTeamId || teamId === eventTeamId);
  });
  const eventStatus = reviewEvent
    ? statusValue(reviewEvent.status === 'failed' && key === 'evidence-merge' ? 'partial' : reviewEvent.status)
    : undefined;
  const valueStatus = statusValue(recordValue(value, 'status', 'review_status', 'reviewStatus'));
  const valueStatusText = normalizeText(
    recordValue(value, 'status', 'review_status', 'reviewStatus'),
    32,
  ).toLowerCase();
  // The event stream is the live source of truth.  A persisted trace can
  // still say "completed" while a later review round is actually running.
  const resolvedStatus = eventStatus || status || valueStatus || (value ? 'completed' : undefined);
  const rawSummary = normalizeText(
    recordValue(value, 'summary', 'conclusion', 'rationale') ?? reviewEvent?.summary,
    600,
  );
  const isDefaultValue = ['not_started', 'queued', 'pending', ''].includes(valueStatusText);
  // A review lane is evidence of an actual server-side review event or a
  // meaningful persisted result.  A Team trace initializes several review
  // fields with ``not_started`` objects; those objects are bookkeeping, not a
  // user-visible review phase.  Rendering them made the UI show a static
  // checklist before the server had dispatched any reviewer.
  if (!reviewEvent && (!value || (isDefaultValue && !rawSummary))) return undefined;
  if (!resolvedStatus && !value && !reviewEvent) return undefined;
  return {
    key,
    label,
    status: resolvedStatus || 'queued',
    summary: reviewSummaryForStatus(rawSummary, resolvedStatus),
  };
};

export const memberStatus = (
  result: TraceRecord | undefined,
  workerEvents: readonly AgentStageEvent[],
): TeamMemberStatus => {
  const lifecycleStatus = workerEvents.filter((event) => (
    event.stage === 'planning'
    && Boolean(event.actionId)
    && event.actionId?.endsWith(':worker')
  ));
  const latestWorkerEvent = latestByOrder(lifecycleStatus);
  const workerStatus = stageOutcomeStatus(latestWorkerEvent) || statusValue(latestWorkerEvent?.status);
  const workerAttemptValue = Number(recordValue(latestWorkerEvent?.details, 'attempt'));
  const resultAttemptValue = Number(recordValue(result, 'attempt'));
  const workerAttempt = Number.isFinite(workerAttemptValue) ? workerAttemptValue : undefined;
  const resultAttempt = Number.isFinite(resultAttemptValue) ? resultAttemptValue : undefined;
  // A fresh worker start is the only state that may override a persisted
  // result: it represents a currently running re-execution. Terminal worker
  // failures are kept as an event-level detail when the durable report is
  // already partial, preserving the Team-level outcome shown to the user.
  const rawResultStatus = statusValue(result?.status);
  const resultErrorCode = normalizeText(
    recordValue(result, 'error_code', 'errorCode'),
    128,
  );
  // A recovered tool error remains in the audit trail, not in the worker's
  // terminal verdict. Only the latest worker lifecycle or its report owns it.
  const hasWorkerError = Boolean(resultErrorCode || (
    latestWorkerEvent?.errorCode
    && ['failed', 'blocked', 'cancelled'].includes(latestWorkerEvent.status)
    && workerStatus !== 'completed'
  ));
  // A worker result carrying an error receipt is not a clean completion even
  // when an older/stale trace says ``status=completed``. Tool parts can still
  // be complete; the worker handoff must remain partial until its report and
  // criteria are valid.
  const resultStatus = rawResultStatus === 'completed' && hasWorkerError
    ? 'partial'
    : rawResultStatus;
  const resultIsTerminal = Boolean(resultStatus && terminalStatuses.has(resultStatus));
  const comparableAttempts = workerAttempt !== undefined && resultAttempt !== undefined;
  const workerIsNewer = !resultIsTerminal
    || !comparableAttempts
    || (workerAttempt as number) > (resultAttempt as number);
  if ((workerStatus === 'running' || workerStatus === 'waiting') && workerIsNewer) {
    return workerStatus;
  }
  // Results are the authoritative state once the same attempt has reached a
  // terminal report.  A later attempt may override it, but an old start event
  // must not leave the member spinning after the report and the Team result
  // have already been persisted.
  if (resultStatus) return resultStatus;
  if (workerStatus) return workerStatus;
  return 'queued';
};

export const terminalMemberStatus = (
  status: TeamMemberStatus,
  runStatus: TeamMemberStatus | undefined,
): TeamMemberStatus => {
  if (!runStatus || terminalStatuses.has(status)) return status;
  if (runStatus === 'cancelled') return 'cancelled';
  if (runStatus === 'blocked') return 'blocked';
  if (runStatus === 'failed') return 'failed';
  if (runStatus === 'partial') return 'partial';
  if (runStatus === 'completed') return 'completed';
  return status;
};

export const memberProgress = (
  workerEvents: readonly AgentStageEvent[],
  projectedProgress: string,
): string => {
  if (projectedProgress) return projectedProgress;
  const lifecycleProgress = [...workerEvents]
    .reverse()
    .filter((event) => (
      event.stage === 'planning'
      && Boolean(event.actionId)
      && event.actionId?.endsWith(':worker')
    ))
    .map(teamProgressText)
    .find(Boolean);
  if (lifecycleProgress) return lifecycleProgress;
  const progress = [...workerEvents].reverse().map(teamProgressText).find(Boolean);
  if (progress) return progress;
  const latest = latestByOrder(workerEvents);
  if (latest?.summary) return latest.summary;
  return '';
};

export const memberSummary = (result: TraceRecord | undefined): string => (
  normalizeText(recordValue(result, 'summary'), 2_400)
);

export const uniqueProgress = (values: readonly string[]): string[] => {
  const seen = new Set<string>();
  return values.filter((value) => {
    const normalized = value.replace(/\s+/g, ' ').trim();
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
};
