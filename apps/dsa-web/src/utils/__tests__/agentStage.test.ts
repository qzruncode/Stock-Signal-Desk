import { describe, expect, it } from 'vitest';
import {
  agentStageLabel,
  latestAgentStageEvent,
  reasoningStatusLabel,
} from '../agentStage';

describe('LangGraph agent stages', () => {
  it('selects the latest native event and accepts backend snake_case fields', () => {
    expect(latestAgentStageEvent([
      { unrelated: true },
      {
        event: 'agent_stage',
        run_id: 'run-1',
        stage: 'execute',
        status: 'started',
        action_id: 'query-a',
        error_code: null,
        summary: '正在执行一个原子查询',
      },
    ])).toEqual({
      event: 'agent_stage',
      runId: 'run-1',
      stage: 'execute',
      status: 'started',
      actionId: 'query-a',
      errorCode: null,
      summary: '正在执行一个原子查询',
      occurredAt: undefined,
    });
  });

  it('ignores malformed events and exposes only generic control labels', () => {
    expect(latestAgentStageEvent([{ event: 'agent_stage', status: 'started' }])).toBeNull();
    expect(agentStageLabel('understand')).toBe('理解目标');
    expect(agentStageLabel('discover')).toBe('检索工具');
    expect(agentStageLabel('plan')).toBe('动态规划');
    expect(agentStageLabel('approval')).toBe('等待审批');
    expect(agentStageLabel('reflect')).toBe('检查完成度');
    expect(agentStageLabel('verify')).toBe('核对证据');
    expect(agentStageLabel('future_stage')).toBe('处理中');
  });

  it('hydrates historical v2 task ids only as read-only action ids', () => {
    const event = latestAgentStageEvent([{
      event: 'agent_stage_v2',
      run_id: 'legacy-run',
      stage: 'execution',
      status: 'completed',
      task_id: 'legacy-task',
      summary: '历史事件',
    }]);

    expect(event?.actionId).toBe('legacy-task');
    expect(event?.event).toBe('agent_stage_v2');
  });

  it('returns a stable snapshot and truthful terminal status', () => {
    const raw = {
      event: 'agent_stage',
      run_id: 'run-stable',
      stage: 'publish',
      status: 'completed',
      summary: '回答完成',
    };
    const completed = latestAgentStageEvent([raw]);
    const failed = latestAgentStageEvent([{
      event: 'agent_stage',
      run_id: 'run-failed',
      stage: 'verify',
      status: 'failed',
      error_code: 'claim_evidence_gap',
      summary: '证据不足',
    }]);

    expect(latestAgentStageEvent([raw])).toBe(completed);
    expect(reasoningStatusLabel(true, failed)).toBe('分析与执行中');
    expect(reasoningStatusLabel(false, failed)).toBe('失败');
    expect(reasoningStatusLabel(false, completed)).toBe('完成');
    expect(reasoningStatusLabel(false, null)).toBe('状态未知');
    expect(reasoningStatusLabel(false, latestAgentStageEvent([{
      event: 'agent_stage',
      run_id: 'run-cancelled',
      stage: 'publish',
      status: 'cancelled',
      summary: '用户停止',
    }]))).toBe('已取消');
  });
});
