import type { AgentStageEvent } from '../../utils/agentStage';
import { isRecord } from './AgentReasoningUtils';

export const runtimeErrorId = (event: AgentStageEvent | undefined): string => {
  const receipt = event?.details?.runtime_error;
  return isRecord(receipt) ? String(receipt.error_id ?? receipt.errorId ?? '') : '';
};

/** Recovery updates a receipt, not the failed tool's execution outcome. */
export const teamRuntimeErrors = (
  events: readonly AgentStageEvent[],
  toolResults: readonly Record<string, unknown>[] = [],
): AgentStageEvent[] => {
  const errors = new Map<string, AgentStageEvent>();
  const apply = (receipt: Record<string, unknown>, origin: AgentStageEvent) => {
    const id = String(receipt.error_id ?? receipt.errorId ?? '');
    if (!id) return;
    const previous = errors.get(id);
    errors.set(id, {
      ...origin, ...previous,
      stage: 'runtime_error',
      status: 'failed',
      actionId: previous?.actionId ?? String(receipt.action_id ?? receipt.actionId ?? origin.actionId ?? ''),
      toolCallId: previous?.toolCallId ?? String(receipt.tool_call_id ?? receipt.toolCallId ?? ''),
      details: { ...origin.details, ...previous?.details, runtime_error: receipt },
    });
  };
  events.forEach((event, index) => {
    if (event.stage === 'runtime_error') {
      const receipt = event.details?.runtime_error;
      if (isRecord(receipt) && (receipt.error_id || receipt.errorId)) apply(receipt, event);
      else errors.set(`event:${index}`, event);
    }
    if (event.stage === 'source_fallback' && Array.isArray(event.details?.runtime_errors)) {
      event.details.runtime_errors.filter(isRecord).forEach((receipt) => apply(receipt, event));
    }
  });
  // Terminal trace is the durable version of the same receipts, including
  // when the early stage events have been compacted out of the replay.
  toolResults.forEach((record) => {
    const receipts = record.runtime_errors ?? record.runtimeErrors;
    if (!Array.isArray(receipts)) return;
    receipts.filter(isRecord).forEach((receipt) => apply(receipt, {
      event: 'agent_stage', stage: 'runtime_error', status: 'failed',
      runId: String(receipt.run_id ?? receipt.runId ?? ''),
      summary: String(receipt.message ?? ''),
      details: {
        team_id: receipt.collaboration_id ?? receipt.collaborationId,
        task_id: receipt.task_id ?? receipt.taskId,
        agent_id: receipt.agent_id ?? receipt.agentId,
      },
    }));
  });
  const resolved = [...errors.values()];
  const receiptActions = new Set(resolved.filter(runtimeErrorId).map((event) => event.actionId).filter(Boolean));
  // Bounded stage history may retain an error event without its receipt.
  // The durable receipt for that exact scoped call replaces this incomplete
  // copy; never collapse distinct calls or retries by tool name alone.
  return resolved.filter((event) => runtimeErrorId(event) || !event.actionId || !receiptActions.has(event.actionId));
};

export const recoveryStatusLabel = (status: string): string => ({
  pending: '待恢复', started: '恢复中', completed: '已返回，待核验覆盖',
  failed: '恢复未成功', skipped: '未执行（工具或预算受限）', not_attempted: '未尝试',
}[status] ?? status);
