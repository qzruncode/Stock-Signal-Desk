import { toCamelCase } from "../utils";
import type { AgentExecutionTrace, ChatConversationDetail, ChatConversationMessage, PendingAgentInterrupt } from "./types";

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
const CLIENT_GOAL_MAX_OBJECT_KEYS = 48;
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
  ['goal', 'goal', 1],
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
      snakeKey === 'team'
        ? CLIENT_TEAM_MAX_OBJECT_KEYS
        : snakeKey === 'goal'
          ? CLIENT_GOAL_MAX_OBJECT_KEYS
          : CLIENT_TRACE_MAX_OBJECT_KEYS,
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
export const normalizeConversationDetail = (payload: Record<string, unknown>): ChatConversationDetail => {
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
