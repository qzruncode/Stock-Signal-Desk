import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { isRecord } from './AgentReasoningUtils';
import { type TeamMemberModel, type TeamMemberStatus, type TeamPartRecord, type TeamToolRecord } from './TeamBoardUtils';
export const terminalStatuses = new Set<TeamMemberStatus>([
  'completed',
  'partial',
  'failed',
  'blocked',
  'cancelled',
]);

export const statusLabel = (status: TeamMemberStatus): string => {
  switch (status) {
    case 'completed': return '已完成';
    case 'partial': return '部分完成';
    case 'failed': return '失败';
    case 'blocked': return '已阻塞';
    case 'cancelled': return '已取消';
    case 'waiting': return '等待中';
    case 'queued': return '待开始';
    default: return '执行中';
  }
};

export const statusProblem = (status: TeamMemberStatus): boolean => (
  status === 'partial'
  || status === 'failed'
  || status === 'blocked'
  || status === 'cancelled'
);

export const statusClassName = (status: TeamMemberStatus): string => (
  statusProblem(status)
    ? 'text-amber-700'
    : status === 'completed'
      ? 'text-emerald-700'
      : status === 'queued'
        || status === 'waiting'
        ? 'text-muted-foreground'
        : 'text-primary'
);

export const roleIconClassName = (role: TeamMemberModel['role']): string => {
  switch (role) {
    case 'market': return 'text-sky-600';
    case 'fundamental': return 'text-violet-600';
    case 'news': return 'text-amber-600';
    default: return 'text-muted-foreground';
  }
};

export const parseArgs = (part: TeamPartRecord): Record<string, unknown> => {
  const raw = part.args;
  if (isRecord(raw)) return raw;
  const argsText = typeof part.argsText === 'string'
    ? part.argsText
    : typeof part.args_text === 'string'
      ? part.args_text
      : '';
  if (!argsText.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(argsText);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

export const toolPartProps = (record: TeamToolRecord): ToolCallMessagePartProps => {
  const { part, event } = record;
  const result = part.result;
  const hasResult = Object.prototype.hasOwnProperty.call(part, 'result')
    || terminalStatuses.has(event?.status as TeamMemberStatus);
  const failed = Boolean(part.isError)
    || event?.status === 'failed'
    || event?.status === 'blocked'
    || event?.status === 'cancelled';
  return {
    type: 'tool-call',
    toolCallId: String(part.toolCallId ?? part.tool_call_id ?? event?.toolCallId ?? event?.actionId ?? 'team-tool'),
    toolName: String(part.toolName ?? part.tool_name ?? '原子工具'),
    args: parseArgs(part),
    argsText: String(part.argsText ?? part.args_text ?? ''),
    ...(Object.prototype.hasOwnProperty.call(part, 'result') ? { result } : {}),
    isError: failed,
    status: hasResult
      ? { type: 'complete', reason: 'stop' }
      : { type: 'running' },
    addResult: () => undefined,
    resume: () => undefined,
    respondToApproval: () => undefined,
  } as ToolCallMessagePartProps;
};
