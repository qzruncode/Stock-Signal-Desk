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
  errorCode?: string | null;
  summary: string;
  occurredAt?: string;
}

/** Read-only type alias for hydrated messages written by the removed engine. */
export type AgentStageEventV2 = AgentStageEvent;

const STAGE_LABELS: Record<string, string> = {
  understand: '理解目标',
  discover: '检索工具',
  plan: '动态规划',
  policy: '安全检查',
  approval: '等待审批',
  execute: '执行工具',
  reflect: '检查完成度',
  answer: '整理回答',
  verify: '核对证据',
  publish: '发布回答',
  // Historical display only. These values can never route a new run.
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

const parsedEventCache = new WeakMap<Record<string, unknown>, AgentStageEvent | null>();

export function latestAgentStageEvent(values: readonly unknown[] | undefined): AgentStageEvent | null {
  if (!values?.length) return null;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const raw = values[index];
    if (!isRecord(raw) || (raw.event !== 'agent_stage' && raw.event !== 'agent_stage_v2')) continue;
    const cached = parsedEventCache.get(raw);
    if (cached) return cached;
    const runId = typeof raw.run_id === 'string' ? raw.run_id : typeof raw.runId === 'string' ? raw.runId : '';
    const stage = typeof raw.stage === 'string' ? raw.stage : '';
    const status = typeof raw.status === 'string' ? raw.status : '';
    if (
      !runId
      || !stage
      || !['started', 'completed', 'succeeded', 'failed', 'blocked', 'cancelled'].includes(status)
    ) {
      parsedEventCache.set(raw, null);
      continue;
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
    parsedEventCache.set(raw, parsed);
    return parsed;
  }
  return null;
}

export function agentStageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? '处理中';
}

export function reasoningStatusLabel(
  running: boolean,
  event: AgentStageEvent | null,
): '分析与执行中' | '完成' | '失败' | '已取消' | '状态未知' {
  if (running) return '分析与执行中';
  if (event?.status === 'cancelled') return '已取消';
  if (event?.status === 'failed' || event?.status === 'blocked') return '失败';
  if (
    event?.status === 'completed'
    || (event?.stage === 'completed' && event.status === 'succeeded')
  ) return '完成';
  return '状态未知';
}
