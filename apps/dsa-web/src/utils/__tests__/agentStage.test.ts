import { describe, expect, it } from 'vitest';
import {
  agentStageLabel,
  latestAgentStageEvent,
  reasoningStatusLabel,
} from '../agentStage';

describe('agent stage v2', () => {
  it('selects the latest v2 event and accepts backend snake_case fields', () => {
    expect(latestAgentStageEvent([
      { unrelated: true },
      {
        event: 'agent_stage_v2',
        run_id: 'run-1',
        stage: 'execution',
        status: 'started',
        task_id: 'filter',
        error_code: null,
        summary: '正在执行完整候选集合',
      },
    ])).toEqual({
      event: 'agent_stage_v2',
      runId: 'run-1',
      stage: 'execution',
      status: 'started',
      taskId: 'filter',
      errorCode: null,
      summary: '正在执行完整候选集合',
      occurredAt: undefined,
    });
  });

  it('ignores malformed events and provides non-technical labels', () => {
    expect(latestAgentStageEvent([{ event: 'agent_stage_v2', status: 'started' }])).toBeNull();
    expect(agentStageLabel('resource_binding')).toBe('绑定数据范围');
    expect(agentStageLabel('benefit_outline')).toBe('拆解产业受益链');
    expect(agentStageLabel('catalog_mapping')).toBe('匹配真实板块');
    expect(agentStageLabel('resource_published')).toBe('发布板块集合');
    expect(agentStageLabel('future_stage')).toBe('处理中');
  });

  it('returns a stable snapshot for the same immutable stage event', () => {
    const event = {
      event: 'agent_stage_v2',
      run_id: 'run-stable',
      stage: 'execution',
      status: 'started',
      summary: '正在执行',
    };

    expect(latestAgentStageEvent([event])).toBe(latestAgentStageEvent([event]));
  });

  it('does not present a failed or merely ended reasoning stream as completed', () => {
    const failed = latestAgentStageEvent([{
      event: 'agent_stage_v2',
      run_id: 'run-failed',
      stage: 'outline',
      status: 'failed',
      error_code: 'planner_schema_invalid',
      summary: '能力契约校验失败',
    }]);
    const completed = latestAgentStageEvent([{
      event: 'agent_stage_v2',
      run_id: 'run-completed',
      stage: 'completed',
      status: 'succeeded',
      summary: '回答完成',
    }]);

    expect(reasoningStatusLabel(true, failed)).toBe('分析与执行中');
    expect(reasoningStatusLabel(false, failed)).toBe('失败');
    expect(reasoningStatusLabel(false, completed)).toBe('完成');
    expect(reasoningStatusLabel(false, null)).toBe('状态未知');
    expect(reasoningStatusLabel(false, latestAgentStageEvent([{
      event: 'agent_stage_v2',
      run_id: 'run-cancelled',
      stage: 'completed',
      status: 'cancelled',
      summary: '用户停止',
    }]))).toBe('已取消');
  });
});
