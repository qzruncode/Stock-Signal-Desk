export type AgentStageStatusV2 = 'started' | 'succeeded' | 'failed' | 'blocked' | 'cancelled';

export interface AgentStageEventV2 {
  event: 'agent_stage_v2';
  runId: string;
  stage: string;
  status: AgentStageStatusV2;
  taskId?: string | null;
  errorCode?: string | null;
  summary: string;
  occurredAt?: string;
}

const STAGE_LABELS: Record<string, string> = {
  outline: '理解任务',
  parameterization: '确认条件',
  normalization: '确定执行口径',
  resource_binding: '绑定数据范围',
  compilation: '生成执行流程',
  policy: '安全校验',
  execution: '执行任务',
  benefit_outline: '拆解产业受益链',
  catalog_loading: '载入实时板块目录',
  catalog_mapping: '匹配真实板块',
  result_validation: '核对结果',
  resource_published: '发布板块集合',
  synthesis: '整理回答',
  completed: '完成',
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const parsedEventCache = new WeakMap<Record<string, unknown>, AgentStageEventV2 | null>();

export function latestAgentStageEvent(values: readonly unknown[] | undefined): AgentStageEventV2 | null {
  if (!values?.length) return null;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const raw = values[index];
    if (!isRecord(raw) || raw.event !== 'agent_stage_v2') continue;
    const cached = parsedEventCache.get(raw);
    if (cached) return cached;
    const runId = typeof raw.run_id === 'string' ? raw.run_id : typeof raw.runId === 'string' ? raw.runId : '';
    const stage = typeof raw.stage === 'string' ? raw.stage : '';
    const status = typeof raw.status === 'string' ? raw.status : '';
    if (!runId || !stage || !['started', 'succeeded', 'failed', 'blocked', 'cancelled'].includes(status)) {
      parsedEventCache.set(raw, null);
      continue;
    }
    const parsed: AgentStageEventV2 = {
      event: 'agent_stage_v2',
      runId,
      stage,
      status: status as AgentStageStatusV2,
      taskId: typeof raw.task_id === 'string' ? raw.task_id : typeof raw.taskId === 'string' ? raw.taskId : null,
      errorCode: typeof raw.error_code === 'string' ? raw.error_code : typeof raw.errorCode === 'string' ? raw.errorCode : null,
      summary: typeof raw.summary === 'string' ? raw.summary : '',
      occurredAt: typeof raw.occurred_at === 'string' ? raw.occurred_at : typeof raw.occurredAt === 'string' ? raw.occurredAt : undefined,
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
  event: AgentStageEventV2 | null,
): '分析与执行中' | '完成' | '失败' | '已取消' | '状态未知' {
  if (running) return '分析与执行中';
  if (event?.status === 'cancelled') return '已取消';
  if (event?.status === 'failed' || event?.status === 'blocked') return '失败';
  if (event?.stage === 'completed' && event.status === 'succeeded') return '完成';
  return '状态未知';
}
