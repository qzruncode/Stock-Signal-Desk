import { describe, expect, it } from 'vitest';
import type { AgentStageEvent } from '../../utils/agentStage';
import { teamRuntimeErrors, recoveryStatusLabel } from './TeamRuntimeErrors';

const error: AgentStageEvent = {
  event: 'agent_stage', runId: 'run', stage: 'runtime_error', status: 'failed',
  actionId: 'market:quote', summary: 'Provider 超时',
  details: { team_id: 'team', task_id: 'market', runtime_error: {
    error_id: 'error-1', action_id: 'market:quote', fallback_status: 'pending', message: 'Provider 超时',
  } },
};
const recovered = { ...error.details!.runtime_error as Record<string, unknown>,
  fallback_status: 'completed', fallback_call_ids: ['market:web'] };
const recovery: AgentStageEvent = {
  ...error, stage: 'source_fallback', actionId: 'recovery', status: 'completed',
  details: { team_id: 'team', task_id: 'market', runtime_errors: [recovered] },
};

describe('Team recovery receipts', () => {
  it('updates the same error without turning the original failed tool into success', () => {
    const events = teamRuntimeErrors([error, recovery]);
    expect(events).toHaveLength(1);
    expect(events[0].actionId).toBe('market:quote');
    expect(events[0].status).toBe('failed');
    expect(events[0].summary).toBe('Provider 超时');
    expect(events[0].details?.runtime_error).toEqual(recovered);
  });
  it('hydrates the same receipt when only a compacted recovery event remains', () => {
    const events = teamRuntimeErrors([recovery]);
    expect(events).toHaveLength(1);
    expect(events[0].actionId).toBe('market:quote');
    expect(events[0].details?.task_id).toBe('market');
  });
  it('replaces a receipt-less compacted event with the durable receipt for exactly the same call', () => {
    const compacted = { ...error, details: { task_id: 'market' } };
    const unrelated = { ...compacted, actionId: 'market:another-call' };
    const events = teamRuntimeErrors([compacted, unrelated], [{ runtime_errors: [recovered] }]);
    expect(events).toHaveLength(2);
    expect(events.filter((event) => event.actionId === 'market:quote')).toHaveLength(1);
    expect(events.find((event) => event.actionId === 'market:quote')?.details?.runtime_error).toEqual(recovered);
    expect(events.find((event) => event.actionId === 'market:another-call')).toBeDefined();
  });
  it('uses persisted tool receipts on refresh and keeps other failures independent', () => {
    const other = { ...error, details: { runtime_error: { error_id: 'error-2', fallback_status: 'pending' } } };
    const events = teamRuntimeErrors([error, other], [{ runtime_errors: [recovered] }]);
    expect(events).toHaveLength(2);
    expect(events[0].details?.runtime_error).toEqual(recovered);
    expect(events[1].details?.runtime_error).toEqual(other.details.runtime_error);
    expect(recoveryStatusLabel('completed')).toBe('已返回，待核验覆盖');
  });
});
