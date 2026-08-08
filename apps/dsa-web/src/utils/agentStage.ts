export type AgentStageStatus =
  | 'started'
  | 'completed'
  | 'succeeded'
  | 'failed'
  | 'blocked'
  | 'cancelled';

export interface AgentStageEvent {
  event: 'agent_stage' | 'agent_stage_v2';
  runId: string;
  stage: string;
  status: AgentStageStatus;
  actionId?: string | null;
  toolCallId?: string | null;
  roundId?: string | null;
  errorCode?: string | null;
  summary: string;
  occurredAt?: string;
  details?: Record<string, unknown>;
}

/** Read-only type alias for hydrated messages written by the removed engine. */
export type AgentStageEventV2 = AgentStageEvent;

const STAGE_LABELS: Record<string, string> = {
  model: '模型决策',
  tool: '调用工具',
  evidence: '关联证据',
  approval: '等待审批',
  publish: '发布回答',
  // Historical display only. These values can never route a new run.
  understand: '理解目标',
  discover: '选择工具',
  plan: '动态规划',
  policy: '安全检查',
  execute: '执行工具',
  reflect: '检查完成度',
  answer: '整理回答',
  verify: '核对证据',
  outline: '理解任务',
  parameterization: '确认条件',
  normalization: '确定执行口径',
  resource_binding: '绑定数据范围',
  compilation: '生成执行流程',
  execution: '执行任务',
  synthesis: '整理回答',
  completed: '完成',
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

type DetailBudget = { remaining: number };

const boundedStageValue = (
  value: unknown,
  budget: DetailBudget,
  depth = 0,
): unknown => {
  if (budget.remaining <= 0 || depth >= 8) return '[详情已折叠]';
  if (value === null || value === undefined || typeof value === 'boolean' || typeof value === 'number') {
    budget.remaining -= 16;
    return value;
  }
  if (typeof value === 'string') {
    const limit = Math.min(2_400, Math.max(0, budget.remaining - 1));
    const text = value.slice(0, limit);
    budget.remaining -= text.length;
    return value.length > text.length ? `${text}…` : text;
  }
  if (Array.isArray(value)) {
    const items: unknown[] = [];
    for (const item of value.slice(0, 24)) {
      if (budget.remaining <= 0) break;
      items.push(boundedStageValue(item, budget, depth + 1));
    }
    if (value.length > items.length) items.push(`[其余 ${value.length - items.length} 项已折叠]`);
    return items;
  }
  if (isRecord(value)) {
    const result: Record<string, unknown> = {};
    let count = 0;
    for (const key in value) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
      if (count >= 32 || budget.remaining <= 0) {
        result._truncated = true;
        break;
      }
      count += 1;
      result[key.slice(0, 96)] = boundedStageValue(value[key], budget, depth + 1);
    }
    return result;
  }
  return String(value).slice(0, 240);
};

const boundedStageDetails = (details: Record<string, unknown>): Record<string, unknown> => {
  const value = boundedStageValue(details, { remaining: 24_000 });
  return isRecord(value) ? value : {};
};

const parsedEventCache = new WeakMap<Record<string, unknown>, AgentStageEvent | null>();

function parseAgentStageEvent(raw: Record<string, unknown>): AgentStageEvent | null {
  if (raw.event !== 'agent_stage' && raw.event !== 'agent_stage_v2') return null;
  if (parsedEventCache.has(raw)) return parsedEventCache.get(raw) ?? null;
  const runId = typeof raw.run_id === 'string' ? raw.run_id : typeof raw.runId === 'string' ? raw.runId : '';
  const stage = typeof raw.stage === 'string' ? raw.stage : '';
  const status = typeof raw.status === 'string' ? raw.status : '';
  if (
    !runId
    || !stage
    || !['started', 'completed', 'succeeded', 'failed', 'blocked', 'cancelled'].includes(status)
  ) {
    parsedEventCache.set(raw, null);
    return null;
  }
  const parsed: AgentStageEvent = {
    event: raw.event,
    runId,
    stage,
    status: status as AgentStageStatus,
    actionId: typeof raw.action_id === 'string'
      ? raw.action_id
      : typeof raw.actionId === 'string'
        ? raw.actionId
        : typeof raw.task_id === 'string'
          ? raw.task_id
          : null,
    toolCallId: typeof raw.tool_call_id === 'string'
      ? raw.tool_call_id
      : typeof raw.toolCallId === 'string'
        ? raw.toolCallId
        : null,
    roundId: typeof raw.round_id === 'string'
      ? raw.round_id
      : typeof raw.roundId === 'string'
        ? raw.roundId
        : null,
    errorCode: typeof raw.error_code === 'string'
      ? raw.error_code
      : typeof raw.errorCode === 'string'
        ? raw.errorCode
        : null,
    summary: typeof raw.summary === 'string' ? raw.summary : '',
    occurredAt: typeof raw.occurred_at === 'string'
      ? raw.occurred_at
      : typeof raw.occurredAt === 'string'
        ? raw.occurredAt
        : undefined,
  };
  const details = raw.details;
  if (isRecord(details)) parsed.details = boundedStageDetails(details);
  parsedEventCache.set(raw, parsed);
  return parsed;
}

export function agentStageEvents(values: readonly unknown[] | undefined): AgentStageEvent[] {
  if (!values?.length) return [];
  return values.reduce<AgentStageEvent[]>((events, raw) => {
    if (!isRecord(raw)) return events;
    const parsed = parseAgentStageEvent(raw);
    if (parsed) events.push(parsed);
    return events;
  }, []);
}

const stageInstanceKey = (event: AgentStageEvent): string => [
  event.runId,
  event.stage,
  event.actionId || '',
  event.toolCallId || '',
  // An action/tool-call id is stable across a recovered LangGraph invocation,
  // while the presentation bridge can legitimately lose its round id.  Keep
  // the action identity authoritative so recovery does not leave an obsolete
  // "running" row beside the terminal result.  Stages without either stable
  // id still use round id to distinguish sequential control-loop passes.
  event.actionId || event.toolCallId ? '' : (event.roundId || ''),
].join('|');

const compatibleOpenStageKey = (
  open: ReadonlyMap<string, AgentStageEvent>,
  event: AgentStageEvent,
): string | null => {
  const exact = stageInstanceKey(event);
  if (open.has(exact)) return exact;
  // A recovered graph reconstructs a new presentation bridge.  Its closing
  // event can therefore lack the round id emitted before process restart.
  // Match the most recent compatible stage instance instead of leaving it
  // falsely open and synthesising a duplicate failure at publication.
  for (const [key, active] of [...open.entries()].reverse()) {
    if (
      active.runId === event.runId
      && active.stage === event.stage
      && (active.actionId || '') === (event.actionId || '')
      && (active.toolCallId || '') === (event.toolCallId || '')
      && (!active.roundId || !event.roundId || active.roundId === event.roundId)
    ) {
      return key;
    }
  }
  return null;
};

/**
 * Make legacy traces truthful when an exceptional terminal event was persisted
 * without closing the stage that was active at the time of the failure.
 */
export function reconcileTerminalStageEvents(
  events: readonly AgentStageEvent[],
): AgentStageEvent[] {
  const reconciled: AgentStageEvent[] = [];
  const open = new Map<string, AgentStageEvent>();
  const terminalRuns = new Set<string>();
  for (const event of events) {
    // A run id is single-use. Once it has published a terminal outcome,
    // append-only retries or stale browser events must not make the same run
    // appear live again after its failure/completion has been shown.
    if (terminalRuns.has(event.runId)) continue;
    const isTerminalPublication = (
      (event.stage === 'publish' || event.stage === 'completed')
      && event.status !== 'started'
    );
    // A process restart or user cancellation can persist the terminal publish
    // event before an in-flight stage gets its own closing event. A terminal
    // run can never still have an active step, so reconcile every terminal
    // outcome instead of only error-code-bearing failures.
    if (isTerminalPublication && open.size > 0) {
      const closingStatus: AgentStageStatus = event.status === 'cancelled'
        ? 'cancelled'
        : event.status === 'blocked'
          ? 'blocked'
          : event.status === 'failed' || Boolean(event.errorCode)
            ? 'failed'
            : 'completed';
      for (const active of open.values()) {
        const closed: AgentStageEvent = {
          ...active,
          status: closingStatus,
          errorCode: event.errorCode,
          summary: [
            active.summary,
            event.summary || '运行已结束',
          ].filter(Boolean).join('；'),
          occurredAt: event.occurredAt,
        };
        // This function is a presentation projection, not the durable audit
        // log. Replace the unmatched start in place so a recovered/replayed
        // stage cannot remain visibly "进行中" beside a terminal publish.
        const activeIndex = reconciled.indexOf(active);
        if (activeIndex >= 0) reconciled[activeIndex] = closed;
        else reconciled.push(closed);
      }
      open.clear();
    }
    if (event.status === 'started') {
      const key = stageInstanceKey(event);
      const prior = open.get(key);
      if (prior) {
        // A worker can be recovered after a node has started but before it
        // checkpointed. The re-executed node emits the same logical start;
        // retain its newest state rather than rendering multiple live steps.
        const priorIndex = reconciled.indexOf(prior);
        if (priorIndex >= 0) reconciled[priorIndex] = event;
        else reconciled.push(event);
      } else {
        reconciled.push(event);
      }
      open.set(key, event);
    } else {
      const key = compatibleOpenStageKey(open, event);
      const active = key ? open.get(key) : undefined;
      if (active) {
        // A start event is transport progress, while its terminal partner has
        // the durable outcome and useful details. Keep one truthful timeline
        // node instead of a completed row plus an obsolete spinner row.
        const activeIndex = reconciled.indexOf(active);
        if (activeIndex >= 0) reconciled[activeIndex] = event;
        else reconciled.push(event);
        open.delete(key!);
      } else {
        reconciled.push(event);
      }
    }
    if (isTerminalPublication) {
      terminalRuns.add(event.runId);
    }
  }
  return reconciled;
}

export function latestAgentStageEvent(values: readonly unknown[] | undefined): AgentStageEvent | null {
  return agentStageEvents(values).at(-1) ?? null;
}

export function agentStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? '处理中';
}

export function reasoningStatusLabel(
  running: boolean,
  event: AgentStageEvent | null,
): '分析与执行中' | '完成' | '部分完成' | '失败' | '已取消' | '状态未知' {
  if (running) return '分析与执行中';
  if (event?.status === 'cancelled') return '已取消';
  if (event?.status === 'failed' || event?.status === 'blocked') return '失败';
  if (event?.errorCode) return '部分完成';
  if (
    event?.status === 'completed'
    || (event?.stage === 'completed' && event.status === 'succeeded')
  ) return '完成';
  return '状态未知';
}
