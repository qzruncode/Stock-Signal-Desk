import apiClient from './index';
import { toCamelCase } from './utils';

export interface ChatConversationItem {
  id: string;
  title: string;
  titleSource: 'auto' | 'manual' | string;
  previewText?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ChatConversationMessage {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
  sequence: number;
  createdAt: string;
}

export interface ChatConversationThreadStateMessage {
  message: Record<string, unknown>;
  parentId: string | null;
  runConfig?: Record<string, unknown>;
}

export interface ChatConversationThreadState {
  headId?: string | null;
  messages: ChatConversationThreadStateMessage[];
}

export interface PersistedAgentStage {
  event: 'agent_stage' | 'agent_stage_v2';
  runId?: string;
  run_id?: string;
  stage: string;
  status: 'started' | 'completed' | 'succeeded' | 'failed' | 'blocked' | 'cancelled';
  taskId?: string | null;
  task_id?: string | null;
  actionId?: string | null;
  action_id?: string | null;
  toolCallId?: string | null;
  tool_call_id?: string | null;
  roundId?: string | null;
  round_id?: string | null;
  errorCode?: string | null;
  error_code?: string | null;
  summary?: string;
  occurredAt?: string;
  occurred_at?: string;
  details?: Record<string, unknown>;
}

export type AgentAnswerBlockPresentation =
  | 'markdown'
  | 'table'
  | 'code'
  | 'json'
  | 'list'
  | 'quote'
  | string;

export interface StructuredAnswerBlockProjection {
  section: string;
  kind: string;
  presentationType: AgentAnswerBlockPresentation;
  language: string;
  content: string;
  evidenceIds: string[];
  artifactRefs?: StructuredAnswerArtifactReference[];
  chartRefs?: StructuredAnswerChartReference[];
  actionRefs?: StructuredAnswerActionReference[];
}

export interface StructuredAnswerArtifactReference {
  artifactId: string;
  artifactType: 'file' | 'document' | string;
  title: string;
  mimeType: string;
  downloadUrl: string;
  previewUrl?: string | null;
  actionId?: string | null;
  evidenceId?: string | null;
  toolName?: string | null;
}

export interface StructuredAnswerChartSeries {
  key: string;
  label: string;
}

export interface StructuredAnswerChartReference {
  chartId: string;
  chartType: 'line' | 'bar' | 'area' | string;
  title: string;
  xKey: string;
  series: StructuredAnswerChartSeries[];
  data: Array<Record<string, string | number>>;
  actionId?: string | null;
  evidenceId?: string | null;
}

export interface StructuredAnswerActionReference {
  actionId: string;
  toolName?: string | null;
  effect: 'read' | 'side_effect' | string;
  status: 'completed' | 'failed' | string;
  success: boolean;
  reused: boolean;
  evidenceId?: string | null;
}

export interface StructuredAnswerProjection {
  profile: 'general' | 'research' | string;
  title: string;
  blocks: StructuredAnswerBlockProjection[];
}

export interface AgentPlanningTrace {
  enabled: boolean;
  mode: string;
  status: string;
  revision: number;
  replanCount: number;
  replanLimit: number;
  modelCallCount: number;
  currentStepId?: string | null;
  error?: string | null;
  decision?: { mode: string; reason: string } | null;
  plan?: Record<string, unknown> | null;
  stepReports?: Record<string, unknown>[];
  updates?: PersistedAgentStage[];
}

export interface AgentTeamResult {
  taskId?: string;
  agentId?: string;
  agentNode?: string;
  role?: string;
  status?: string;
  /** Server-owned retry attempt for this logical task. */
  attempt?: number;
  summary?: string;
  findings?: string[];
  findingEvidenceIds?: string[][];
  limitations?: string[];
  openQuestions?: string[];
  confidence?: string;
  failureStrategy?: string;
  evidenceIds?: string[];
  toolCallCount?: number;
  modelTurnCount?: number;
  assessmentStatus?: string;
  criteriaStatus?: string;
  criteriaChecks?: Record<string, unknown>[];
  unmetCriteria?: string[];
  errorCode?: string | null;
  errorDetail?: string | null;
  [key: string]: unknown;
}

export interface AgentTeamWorkerHandoff {
  status?: string;
  taskIds?: string[];
  incompleteTaskIds?: string[];
  error?: string | null;
}

export interface AgentTeamFailurePolicy {
  action?: string;
  taskIds?: string[];
  status?: string;
  error?: string | null;
}

export interface AgentTeamFailure {
  status?: string;
  errorCode?: string | null;
  detail?: string | null;
  phase?: string | null;
  dispatchStatus?: string | null;
}

export interface AgentTeamTrace {
  agentMode?: string;
  resolvedAgentMode?: string;
  mode: string;
  requestedMode?: string;
  route?: string;
  executionStrategy?: string;
  routeReason?: string;
  teamId?: string;
  status?: string;
  workerCount?: number;
  completedWorkerCount?: number;
  planSource?: string;
  planError?: string | null;
  contractCallCount?: number;
  plan?: Record<string, unknown> | null;
  results?: AgentTeamResult[];
  review?: Record<string, unknown> | null;
  reviewStatus?: string;
  reviewError?: string | null;
  evidenceMerge?: Record<string, unknown> | null;
  evidenceMergeStatus?: string;
  conflict?: Record<string, unknown> | null;
  conflictStatus?: string;
  critic?: Record<string, unknown> | null;
  criticStatus?: string;
  criteriaAssessment?: Record<string, unknown> | null;
  criteriaStatus?: string;
  criteriaError?: string | null;
  bullCase?: Record<string, unknown> | null;
  bullCaseStatus?: string;
  bullCaseError?: string | null;
  bearCase?: Record<string, unknown> | null;
  bearCaseStatus?: string;
  bearCaseError?: string | null;
  consensus?: Record<string, unknown> | null;
  consensusStatus?: string;
  dispatchedTaskIds?: string[];
  dispatchRound?: number;
  taskAttempts?: Record<string, number>;
  workerHandoff?: AgentTeamWorkerHandoff | null;
  failurePolicy?: AgentTeamFailurePolicy | null;
  planningHandoff?: Record<string, unknown> | null;
  planningHandoffStatus?: string;
  planningHandoffError?: string | null;
  failure?: AgentTeamFailure | null;
}

export interface AgentExecutionTrace {
  /** Product mode selected for this turn: auto, direct, plan, or team. */
  agentMode?: string;
  /** Effective route selected by Auto, when Auto was requested. */
  resolvedAgentMode?: string | null;
  /** Ordered, bounded assistant-stream parts used for terminal replay. */
  displayPartsVersion?: number;
  displayParts?: Record<string, unknown>[];
  stages?: PersistedAgentStage[];
  actions?: Record<string, unknown>[];
  toolResults?: Record<string, unknown>[];
  evidence?: Record<string, unknown>[];
  claimEvidence?: Record<string, unknown>[];
  structuredAnswer?: StructuredAnswerProjection | null;
  planning?: AgentPlanningTrace | null;
  team?: AgentTeamTrace | null;
  loop?: Record<string, unknown>;
  completedToolCallIds?: string[];
  /** Defensive marker when a malformed API response was compacted for rendering. */
  clientTraceTruncated?: boolean;
}

/** One bounded execution projection belonging to one durable conversation turn. */
export interface AgentExecutionTraceRecord {
  runId: string;
  status?: string | null;
  errorCode?: string | null;
  finalText?: string | null;
  latestStage?: PersistedAgentStage | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  executionTrace?: AgentExecutionTrace | null;
}

export interface AgentCheckpointTaskSummary {
  id?: string | null;
  name?: string | null;
  interruptCount: number;
  hasError: boolean;
  errorType?: string | null;
}

export interface AgentCheckpointMetadata {
  source?: string | null;
  step?: number | null;
  writeKeys: string[];
}

export interface AgentCheckpointStateSummary {
  agentMode?: string;
  resolvedAgentMode?: string | null;
  runId?: string | null;
  conversationId?: string | null;
  status?: string | null;
  messageCount: number;
  toolResultCount: number;
  evidenceCount: number;
  modelTurnCount: number;
  toolCallCount: number;
  evidenceRepairCount: number;
  planningEnabled?: boolean;
  planningMode?: string;
  planningStatus?: string;
  planningRevision?: number;
  planningCurrentStepId?: string | null;
  planningReplanCount?: number;
  planningStepCount?: number;
  hasPendingInterrupt: boolean;
  hasAnswer: boolean;
}

/** Bounded, read-only projection of a native LangGraph checkpoint. */
export interface AgentCheckpointSummary {
  checkpointId?: string | null;
  parentCheckpointId?: string | null;
  createdAt?: string | null;
  metadata: AgentCheckpointMetadata;
  next: string[];
  tasks: AgentCheckpointTaskSummary[];
  state: AgentCheckpointStateSummary;
}

export interface AgentCheckpointHistoryResponse {
  conversationId: string;
  threadId: string;
  currentCheckpointId?: string | null;
  checkpointAuthority: string;
  runLifecycleAuthority: string;
  readOnly: boolean;
  items: AgentCheckpointSummary[];
  hasMore: boolean;
  nextBeforeCheckpointId?: string | null;
}

export interface PendingAgentInterrupt {
  interruptId: string;
  runId: string;
  conversationId?: string;
  fingerprint: string;
  actionId?: string;
  toolName: string;
  summary: string;
  arguments: Record<string, unknown>;
  createdAt?: string;
}

export interface ChatConversationDetail extends ChatConversationItem {
  messages: ChatConversationMessage[];
  threadState?: ChatConversationThreadState | null;
  executionTrace?: AgentExecutionTrace | null;
  /** Per-run trace projections used to restore process disclosures in history. */
  executionTraces?: AgentExecutionTraceRecord[] | null;
  /** 后端是否仍在生成该对话的回复(刷新后前端据此判断是否续流)。 */
  isGenerating?: boolean;
  resumeState?: {
    runId?: string | null;
    active: boolean;
    isGenerating?: boolean;
    status?: 'running' | 'completed' | 'partial' | 'failed' | 'cancelled' | string | null;
    afterChunkIndex: number;
    eventCursor?: number;
    assistantText: string;
    hasToolEvents?: boolean;
    latestStage?: PersistedAgentStage | null;
    pendingInterrupt?: PendingAgentInterrupt | null;
  };
  pendingInterrupt?: PendingAgentInterrupt | null;
}

// Conversation detail is a rendering boundary, not an audit export. Project
// the current execution trace before camelcase-keys walks the payload so one
// oversized tool result cannot freeze the chat page during hydration.
// Bound each typed projection independently. A large transcript must not
// consume the evidence catalog's budget (turning valid citations into false
// "unresolved" warnings), or erase Team status. Field/array/depth caps still
// bound the whole response; raw audit payloads never enter the page state.
const CLIENT_TRACE_FIELD_MAX_CHARACTERS = 180_000;
// Stage status is the source of truth for the process projection. Keep a
// separate slice of the client budget for it so verbose tool/evidence payloads
// cannot erase the final Worker outcome during terminal replay.
const CLIENT_STAGE_HISTORY_RESERVE = 64_000;
const CLIENT_TRACE_MAX_DEPTH = 7;
const CLIENT_STRUCTURED_ANSWER_MAX_DEPTH = 10;
const CLIENT_TRACE_MAX_OBJECT_KEYS = 24;
// Team is a typed process projection. Its top-level contract contains more
// than the generic trace object limit (plan/results plus every review status),
// so applying the generic 24-key cap silently turns later phases into
// "queued" during history replay. Keep the Team object bounded, but large
// enough to retain its complete lifecycle contract.
const CLIENT_TEAM_MAX_OBJECT_KEYS = 96;
const CLIENT_TRACE_MAX_TEXT = 1_600;
const CLIENT_DISPLAY_PART_TEXT = 12_000;
const CLIENT_STRUCTURED_ANSWER_TEXT = 12_000;
const CLIENT_STRUCTURED_ANSWER_ARRAY_LIMIT = 120;
const CLIENT_TRACE_FIELD_LIMITS: Array<[string, string, number]> = [
  ['display_parts', 'displayParts', 240],
  // Team member results drive the independent workspaces. Project them before
  // verbose tool/evidence payloads so a large research answer cannot erase the
  // per-member terminal status during refresh replay.
  ['team', 'team', 1],
  // Citations are part of the final answer contract.  Keep the compact
  // evidence/result projections before the verbose stage history so a long
  // run cannot make every hover degrade to "details unavailable" merely
  // because the shared defensive budget was consumed by timeline metadata.
  ['tool_results', 'toolResults', 80],
  ['evidence', 'evidence', 80],
  ['claim_evidence', 'claimEvidence', 80],
  ['structured_answer', 'structuredAnswer', 1],
  ['planning', 'planning', 1],
  ['stages', 'stages', 120],
  ['actions', 'actions', 32],
  ['loop', 'loop', 1],
  ['completed_tool_call_ids', 'completedToolCallIds', 80],
];

type TraceBudget = { remaining: number; exhausted: boolean };

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const comparableChartKey = (value: string): string => (
  value.replace(/[^A-Za-z0-9]/g, '').toLowerCase()
);

/**
 * ``camelcase-keys`` must normalize API fields, but chart data keys are
 * server-owned dynamic series names and their string values must remain
 * aligned with ``series[].key``.  Restore that alignment after normalization.
 */
const normalizeStructuredAnswerChartKeys = (value: unknown): unknown => {
  if (!isRecord(value) || !Array.isArray(value.blocks)) return value;
  return {
    ...value,
    blocks: value.blocks.map((rawBlock) => {
      if (!isRecord(rawBlock) || !Array.isArray(rawBlock.chartRefs)) return rawBlock;
      return {
        ...rawBlock,
        chartRefs: rawBlock.chartRefs.map((rawChart) => {
          if (!isRecord(rawChart) || !Array.isArray(rawChart.series) || !Array.isArray(rawChart.data)) {
            return rawChart;
          }
          const seriesKeys = rawChart.series
            .filter(isRecord)
            .map((series) => series.key)
            .filter((key): key is string => typeof key === 'string' && key.length > 0);
          if (seriesKeys.length === 0) return rawChart;
          return {
            ...rawChart,
            data: rawChart.data.map((rawPoint) => {
              if (!isRecord(rawPoint)) return rawPoint;
              const point: Record<string, string | number> = {};
              if (typeof rawPoint.x === 'string') point.x = rawPoint.x;
              for (const seriesKey of seriesKeys) {
                const sourceKey = Object.keys(rawPoint).find((key) => (
                  key !== 'x' && comparableChartKey(key) === comparableChartKey(seriesKey)
                ));
                const number = sourceKey ? rawPoint[sourceKey] : undefined;
                if (typeof number === 'number' && Number.isFinite(number)) {
                  point[seriesKey] = number;
                }
              }
              return point;
            }),
          };
        }),
      };
    }),
  };
};

const projectTraceValue = (
  value: unknown,
  budget: TraceBudget,
  depth = 0,
  arrayLimit = 16,
  textLimit = CLIENT_TRACE_MAX_TEXT,
  maxDepth = CLIENT_TRACE_MAX_DEPTH,
  nestedArrayLimit = 16,
  maxObjectKeys = CLIENT_TRACE_MAX_OBJECT_KEYS,
): unknown => {
  if (budget.remaining <= 0) {
    budget.exhausted = true;
    return '[执行详情已折叠]';
  }
  if (value === null || typeof value === 'boolean' || typeof value === 'number') {
    budget.remaining -= 24;
    return value;
  }
  if (typeof value === 'string') {
    const projected = value.slice(0, Math.min(textLimit, budget.remaining));
    budget.remaining -= projected.length;
    if (projected.length < value.length) budget.exhausted = true;
    return projected;
  }
  if (depth >= maxDepth) {
    budget.remaining -= 16;
    budget.exhausted = true;
    return '[嵌套详情已折叠]';
  }
  if (Array.isArray(value)) {
    const projected = value.slice(0, arrayLimit).map((item) => (
      projectTraceValue(item, budget, depth + 1, nestedArrayLimit, textLimit, maxDepth, nestedArrayLimit)
    ));
    if (value.length > projected.length) {
      projected.push(`[其余 ${value.length - projected.length} 项已折叠]`);
      budget.exhausted = true;
    }
    return projected;
  }
  if (isRecord(value)) {
    const projected: Record<string, unknown> = {};
    let keyCount = 0;
    for (const key in value) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
      if (keyCount >= maxObjectKeys || budget.remaining <= 0) {
        projected._clientTruncated = true;
        budget.exhausted = true;
        break;
      }
      const safeKey = key.slice(0, 96);
      budget.remaining -= safeKey.length;
      projected[safeKey] = projectTraceValue(
        value[key],
        budget,
        depth + 1,
        nestedArrayLimit,
        textLimit,
        maxDepth,
        nestedArrayLimit,
        maxObjectKeys,
      );
      keyCount += 1;
    }
    return projected;
  }
  const projected = String(value).slice(0, Math.min(textLimit, budget.remaining));
  budget.remaining -= projected.length;
  return projected;
};

const CLIENT_STAGE_TOP_LEVEL_KEYS = [
  'event',
  'run_id',
  'runId',
  'engine',
  'stage',
  'status',
  'action_id',
  'actionId',
  'task_id',
  'taskId',
  'tool_call_id',
  'toolCallId',
  'round_id',
  'roundId',
  'error_code',
  'errorCode',
  // Details is intentionally inserted before the human summary. Worker role
  // and Team identity must survive even when one event reaches its cap.
  'details',
  'summary',
  'occurred_at',
  'occurredAt',
] as const;

const CLIENT_STAGE_DETAIL_KEYS = [
  'team_id',
  'teamId',
  'task_id',
  'taskId',
  'agent_id',
  'agentId',
  'agent_node',
  'agentNode',
  'expert_id',
  'expertId',
  'attempt',
  'max_attempts',
  'maxAttempts',
  'attempts',
  'dispatcher',
  'handoff',
  'policy',
  'action',
  'round',
  'ready_task_ids',
  'readyTaskIds',
  'dispatched_task_ids',
  'dispatchedTaskIds',
  'retry_task_ids',
  'retryTaskIds',
  'dispatch_task_ids',
  'dispatchTaskIds',
  'incomplete_task_ids',
  'incompleteTaskIds',
  'missing_task_ids',
  'missingTaskIds',
  'task_ids',
  'taskIds',
  'statuses',
  'progress_kind',
  'progressKind',
  'planning_phase',
  'planningPhase',
  'phase',
  'step_id',
  'stepId',
  'objective',
  'goal',
  'initial_state',
  'initialState',
  'completed_summary',
  'completedSummary',
  'next_step_id',
  'nextStepId',
  'next_step_reason',
  'nextStepReason',
  'completion_criteria',
  'completionCriteria',
  'criteria_status',
  'criteriaStatus',
  'missing_items',
  'missingItems',
  'observed_facts',
  'observedFacts',
  'evidence_ids',
  'evidenceIds',
  'evidence_id',
  'evidenceId',
  'verdict',
  'reflection_round',
  'reflectionRound',
  'reviewer_mode',
  'reviewerMode',
  'issues',
  'tool_name',
  'toolName',
  'tool_call_id',
  'toolCallId',
  'progress_text',
  'progressText',
  'user_message',
  'userMessage',
  'model_summary',
  'modelSummary',
  'progress_preview',
  'progressPreview',
  'model_turn',
  'modelTurn',
  'error_code',
  'errorCode',
  'status',
  'claim_count',
  'claimCount',
  'fact_claim_count',
  'factClaimCount',
  'inference_claim_count',
  'inferenceClaimCount',
  'reason',
  'result_summary',
  'resultSummary',
  'data_time',
  'dataTime',
  'result_count',
  'resultCount',
  'omitted_result_count',
  'omittedResultCount',
  'source_labels',
  'sourceLabels',
  'reference_links',
  'referenceLinks',
  'result_items',
  'resultItems',
  'errors',
  'success',
  'arguments',
  'argument_keys',
  'argumentKeys',
] as const;

/**
 * Keep every lifecycle event addressable while compacting verbose details.
 * A tail-only fallback would preserve the final status but break chronological
 * Team/Plan replay, so each event receives a small independent allowance.
 */
const projectStageHistoryForClient = (
  value: unknown,
  maxCharacters: number,
): unknown[] | undefined => {
  if (!Array.isArray(value)) return undefined;
  const eventBudget = Math.min(
    1_200,
    Math.max(480, Math.floor(maxCharacters / Math.max(value.length, 1))),
  );
  return value.map((rawStage) => {
    if (!isRecord(rawStage)) return rawStage;
    const stage: Record<string, unknown> = {};
    CLIENT_STAGE_TOP_LEVEL_KEYS.forEach((key) => {
      if (
        key !== 'details'
        && key !== 'summary'
        && key !== 'occurred_at'
        && key !== 'occurredAt'
        && rawStage[key] !== undefined
      ) {
        stage[key] = rawStage[key];
      }
    });
    const rawDetails = rawStage.details;
    if (isRecord(rawDetails)) {
      const details: Record<string, unknown> = {};
      CLIENT_STAGE_DETAIL_KEYS.forEach((key) => {
        if (rawDetails[key] !== undefined) details[key] = rawDetails[key];
      });
      if (Object.keys(details).length > 0) {
        stage.details = projectTraceValue(
          details,
          {
            remaining: Math.max(240, Math.floor(eventBudget * 0.55)),
            exhausted: false,
          },
          0,
          8,
          360,
          4,
          8,
        );
      }
    }
    if (rawStage.summary !== undefined) stage.summary = rawStage.summary;
    if (rawStage.occurred_at !== undefined) stage.occurred_at = rawStage.occurred_at;
    if (rawStage.occurredAt !== undefined) stage.occurredAt = rawStage.occurredAt;
    return projectTraceValue(
      stage,
      { remaining: eventBudget, exhausted: false },
      0,
      8,
      360,
      4,
      8,
    );
  });
};

const projectExecutionTraceForClient = (value: unknown): Record<string, unknown> | undefined => {
  if (!isRecord(value)) return undefined;
  const rawStages = value.stages;
  const stageHistory = Array.isArray(rawStages)
    ? projectStageHistoryForClient(rawStages, CLIENT_STAGE_HISTORY_RESERVE)
    : undefined;
  let truncated = false;
  const projected: Record<string, unknown> = {};
  for (const [snakeKey, camelKey, arrayLimit] of CLIENT_TRACE_FIELD_LIMITS) {
    if (snakeKey === 'stages') continue;
    const raw = value[snakeKey] ?? value[camelKey];
    if (raw === undefined) continue;
    const fieldBudget: TraceBudget = {
      remaining: CLIENT_TRACE_FIELD_MAX_CHARACTERS,
      exhausted: false,
    };
    let textLimit = CLIENT_TRACE_MAX_TEXT;
    if (snakeKey === 'display_parts') {
      textLimit = CLIENT_DISPLAY_PART_TEXT;
    } else if (snakeKey === 'structured_answer') {
      textLimit = CLIENT_STRUCTURED_ANSWER_TEXT;
    }
    projected[snakeKey] = projectTraceValue(
      raw,
      fieldBudget,
      0,
      arrayLimit,
      textLimit,
      snakeKey === 'structured_answer' ? CLIENT_STRUCTURED_ANSWER_MAX_DEPTH : CLIENT_TRACE_MAX_DEPTH,
      snakeKey === 'structured_answer' ? CLIENT_STRUCTURED_ANSWER_ARRAY_LIMIT : 16,
      snakeKey === 'team' ? CLIENT_TEAM_MAX_OBJECT_KEYS : CLIENT_TRACE_MAX_OBJECT_KEYS,
    );
    truncated ||= fieldBudget.exhausted;
  }
  const rawDisplayPartsVersion = value.display_parts_version ?? value.displayPartsVersion;
  if (typeof rawDisplayPartsVersion === 'number' && Number.isFinite(rawDisplayPartsVersion)) {
    projected.display_parts_version = Math.max(0, Math.floor(rawDisplayPartsVersion));
  }
  if (stageHistory) projected.stages = stageHistory;
  if (truncated) projected.client_trace_truncated = true;
  return projected;
};

/**
 * The active conversation contract deliberately excludes the obsolete
 * assistant-ui `thread_state` snapshot. It may contain unbounded historical
 * tool payloads; canonical messages plus the LangGraph trace are the only
 * rendering inputs. Ignore it defensively during a rolling server upgrade.
 */
const normalizeConversationDetail = (payload: Record<string, unknown>): ChatConversationDetail => {
  const presentationPayload: Record<string, unknown> = { ...payload };
  delete presentationPayload.thread_state;
  delete presentationPayload.threadState;
  const rawExecutionTrace = presentationPayload.execution_trace ?? presentationPayload.executionTrace;
  delete presentationPayload.execution_trace;
  delete presentationPayload.executionTrace;
  const rawExecutionTraceHistory = presentationPayload.execution_traces
    ?? presentationPayload.executionTraces;
  delete presentationPayload.execution_traces;
  delete presentationPayload.executionTraces;
  if (Array.isArray(rawExecutionTraceHistory)) {
    presentationPayload.execution_traces = rawExecutionTraceHistory.map((rawRecord) => {
      if (!isRecord(rawRecord)) return rawRecord;
      const record = { ...rawRecord };
      const rawTrace = record.execution_trace ?? record.executionTrace;
      delete record.execution_trace;
      delete record.executionTrace;
      const executionTrace = projectExecutionTraceForClient(rawTrace);
      if (executionTrace) record.execution_trace = executionTrace;
      return record;
    });
  }
  const rawResumeState = presentationPayload.resume_state ?? presentationPayload.resumeState;
  const resumeStatePayload = isRecord(rawResumeState) ? { ...rawResumeState } : undefined;
  if (resumeStatePayload) {
    delete resumeStatePayload.execution_trace;
    delete resumeStatePayload.executionTrace;
    presentationPayload.resume_state = resumeStatePayload;
    delete presentationPayload.resumeState;
  }
  const executionTrace = projectExecutionTraceForClient(rawExecutionTrace);
  if (executionTrace) presentationPayload.execution_trace = executionTrace;
  const rawPending = presentationPayload.pending_interrupt ?? presentationPayload.pendingInterrupt;
  const data = toCamelCase<ChatConversationDetail>(presentationPayload);
  const normalizedExecutionTrace = data.executionTrace
    ? {
        ...data.executionTrace,
        structuredAnswer: normalizeStructuredAnswerChartKeys(data.executionTrace.structuredAnswer) as AgentExecutionTrace["structuredAnswer"],
      }
    : data.executionTrace;
  const normalizedExecutionTraces = Array.isArray(data.executionTraces)
    ? data.executionTraces.map((record) => ({
        ...record,
        ...(record.executionTrace
          ? {
              executionTrace: {
                ...record.executionTrace,
                structuredAnswer: normalizeStructuredAnswerChartKeys(
                  record.executionTrace.structuredAnswer,
                ) as AgentExecutionTrace["structuredAnswer"],
              },
            }
          : {}),
      }))
    : data.executionTraces;
  const normalizedPending = rawPending && typeof rawPending === 'object' && !Array.isArray(rawPending)
    ? {
        ...toCamelCase<PendingAgentInterrupt>(rawPending as Record<string, unknown>),
        arguments: (
          (rawPending as Record<string, unknown>).arguments
          && typeof (rawPending as Record<string, unknown>).arguments === 'object'
          && !Array.isArray((rawPending as Record<string, unknown>).arguments)
        )
          ? (rawPending as Record<string, unknown>).arguments as Record<string, unknown>
          : {},
      }
    : null;
  return {
    ...data,
    ...(normalizedExecutionTrace ? { executionTrace: normalizedExecutionTrace } : {}),
    ...(normalizedExecutionTraces ? { executionTraces: normalizedExecutionTraces } : {}),
    pendingInterrupt: normalizedPending,
    ...(data.resumeState
      ? {
          resumeState: {
            ...data.resumeState,
            pendingInterrupt: normalizedPending,
          },
        }
      : {}),
    messages: (data.messages || []).map((message) => toCamelCase<ChatConversationMessage>(message)),
  };
};

export interface ChatConversationListResponse {
  items: ChatConversationItem[];
  total: number;
  page: number;
  limit: number;
}

export const agentApi = {
  async listConversations(): Promise<ChatConversationListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/agent/conversations');
    const data = toCamelCase<ChatConversationListResponse>(response.data);
    return {
      ...data,
      items: (data.items || []).map((item) => toCamelCase<ChatConversationItem>(item)),
    };
  },

  async createConversation(): Promise<ChatConversationItem> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/agent/conversations');
    return toCamelCase<ChatConversationItem>(response.data);
  },

  async getConversation(conversationId: string, signal?: AbortSignal): Promise<ChatConversationDetail> {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/agent/conversations/${conversationId}`, { signal });
    return normalizeConversationDetail(response.data);
  },

  async getConversationCheckpoints(
    conversationId: string,
    params: { limit?: number; beforeCheckpointId?: string | null } = {},
  ): Promise<AgentCheckpointHistoryResponse> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/agent/conversations/${conversationId}/checkpoints`,
      {
        params: {
          limit: params.limit,
          before_checkpoint_id: params.beforeCheckpointId || undefined,
        },
      },
    );
    const data = toCamelCase<AgentCheckpointHistoryResponse>(response.data);
    return {
      ...data,
      items: (data.items || []).map((item) => toCamelCase<AgentCheckpointSummary>(item)),
    };
  },

  async syncConversationSnapshot(
    conversationId: string,
    payload: {
      messages?: Array<Record<string, unknown>>;
      pruneAgentContextToMessages?: boolean;
    },
  ): Promise<ChatConversationDetail> {
    // LangGraph persists the orchestration state itself.  A thread runtime
    // export is only an opaque renderer cache and can contain complete raw
    // tool payloads, so never send it back to the server during an edit.
    const requestBody: Record<string, unknown> = {};
    if (payload.messages !== undefined) {
      requestBody.messages = payload.messages;
    }
    if (payload.pruneAgentContextToMessages === true) {
      requestBody.prune_agent_context_to_messages = true;
    }
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/agent/conversations/${conversationId}/snapshot`,
      requestBody,
    );
    return normalizeConversationDetail(response.data);
  },

  async renameConversation(conversationId: string, title: string): Promise<ChatConversationItem> {
    const response = await apiClient.patch<Record<string, unknown>>(`/api/v1/agent/conversations/${conversationId}`, {
      title,
    });
    return toCamelCase<ChatConversationItem>(response.data);
  },

  async deleteConversation(conversationId: string): Promise<void> {
    await apiClient.delete(`/api/v1/agent/conversations/${conversationId}`);
  },

  async clearAllConversations(): Promise<number> {
    const response = await apiClient.delete<{ deleted?: number }>('/api/v1/agent/conversations');
    return Number(response.data.deleted || 0);
  },

  async cancelConversationRun(conversationId: string): Promise<boolean> {
    const response = await apiClient.post<{ cancelled: boolean }>(
      `/api/v1/agent/conversations/${conversationId}/cancel`,
    );
    return response.data.cancelled === true;
  },

  async decideInterrupt(
    conversationId: string,
    interruptId: string,
    payload: {
      runId: string;
      fingerprint: string;
      decision: 'approve' | 'reject';
    },
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/agent/conversations/${conversationId}/interrupts/${interruptId}/decision`,
      {
        run_id: payload.runId,
        fingerprint: payload.fingerprint,
        decision: payload.decision,
      },
    );
  },

};
