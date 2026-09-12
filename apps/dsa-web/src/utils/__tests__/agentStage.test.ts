import { describe, expect, it } from 'vitest';
import { stageDetails } from '../../components/assistant-ui/AgentReasoningUtils';
import {
  agentStageEvents,
  agentStageLabel,
  latestAgentStageEvent,
  reconcileTerminalStageEvents,
  reasoningStatusLabel,
} from '../agentStage';

describe('generic Agent loop stage projection', () => {
  it('parses the real model/tool/evidence/publish vocabulary from durable events', () => {
    const events = agentStageEvents([
      {
        event: 'agent_stage',
        run_id: 'run-1',
        stage: 'model',
        status: 'completed',
        summary: '模型请求 1 个原子操作',
        details: { operations: [{ tool_name: 'search_web_source' }] },
      },
      {
        event: 'agent_stage',
        run_id: 'run-1',
        stage: 'tool',
        status: 'completed',
        action_id: 'call-1',
        summary: 'search_web_source 已返回',
      },
    ]);

    expect(events).toHaveLength(2);
    expect(latestAgentStageEvent(events)?.actionId).toBe('call-1');
    expect(agentStageLabel('model')).toBe('模型决策');
    expect(agentStageLabel('tool')).toBe('调用工具');
    expect(agentStageLabel('content_access')).toBe('正文取证');
    expect(agentStageLabel('evidence')).toBe('关联证据');
    expect(agentStageLabel('reflection')).toBe('语义复核');
    expect(agentStageLabel('approval')).toBe('等待审批');
    expect(agentStageLabel('publish')).toBe('发布回答');
  });

  it('keeps only the terminal version of an actual tool call', () => {
    const events = agentStageEvents([
      {
        event: 'agent_stage',
        run_id: 'run-tool',
        stage: 'tool',
        status: 'started',
        action_id: 'call-1',
        tool_call_id: 'lg-1',
        summary: '执行原子工具 search_web_source',
      },
      {
        event: 'agent_stage',
        run_id: 'run-tool',
        stage: 'tool',
        status: 'completed',
        action_id: 'call-1',
        tool_call_id: 'lg-1',
        summary: 'search_web_source 已返回',
      },
    ]);

    expect(reconcileTerminalStageEvents(events)).toEqual([
      expect.objectContaining({ stage: 'tool', status: 'completed', actionId: 'call-1' }),
    ]);
  });

  it('coalesces repeated content-access recovery into one terminal stage', () => {
    const events = agentStageEvents([
      {
        event: 'agent_stage',
        run_id: 'run-content-access',
        stage: 'content_access',
        status: 'started',
        round_id: '4',
        summary: '发现未完成的正文取证，要求模型继续选择或读取来源',
      },
      {
        event: 'agent_stage',
        run_id: 'run-content-access',
        stage: 'content_access',
        status: 'started',
        round_id: '6',
        summary: '发现未完成的正文取证，要求模型继续选择或读取来源',
      },
      {
        event: 'agent_stage',
        run_id: 'run-content-access',
        stage: 'content_access',
        status: 'completed',
        round_id: '8',
        summary: '已完成模型选定正文来源的读取核对，允许进入最终回答检查',
      },
      {
        event: 'agent_stage',
        run_id: 'run-content-access',
        stage: 'publish',
        status: 'completed',
        round_id: '8',
        summary: '已发布最终回答',
      },
    ]);

    expect(reconcileTerminalStageEvents(events).map((event) => `${event.stage}:${event.status}`)).toEqual([
      'content_access:completed',
      'publish:completed',
    ]);
  });

  it('shows a semantic reflection verdict and bounded issues', () => {
    const lines = stageDetails({
      event: 'agent_stage',
      runId: 'run-reflection',
      stage: 'reflection',
      status: 'completed',
      summary: '语义复核发现问题',
      details: {
        verdict: 'revise',
        reflection_round: 1,
        reviewer_mode: 'self_refine',
        summary: '建议收窄到证据能够支持的范围。',
        issues: [{
          block_index: 1,
          category: 'reasoning',
          severity: 'high',
          reason: '现有资料不足以支持确定性预测。',
        }],
      },
    });

    expect(lines.map((line) => line.text)).toEqual([
      '复核结果：需要修订',
      '复核轮次：1',
      '复核方式：受限自复核',
      '复核说明：建议收窄到证据能够支持的范围。',
      '问题 1：第 1 个区块 · reasoning · high：现有资料不足以支持确定性预测。',
    ]);
  });

  it('closes an interrupted in-flight operation when publication is terminal', () => {
    const events = agentStageEvents([
      {
        event: 'agent_stage',
        run_id: 'run-cancelled',
        stage: 'tool',
        status: 'started',
        action_id: 'call-1',
        summary: '执行原子工具 read_rss_source',
      },
      {
        event: 'agent_stage',
        run_id: 'run-cancelled',
        stage: 'publish',
        status: 'cancelled',
        summary: '用户已停止本轮任务',
      },
    ]);

    expect(reconcileTerminalStageEvents(events).map((event) => `${event.stage}:${event.status}`)).toEqual([
      'tool:cancelled',
      'publish:cancelled',
    ]);
  });

  it('does not revive a completed run when a stale event arrives later', () => {
    const events = agentStageEvents([
      {
        event: 'agent_stage',
        run_id: 'run-complete',
        stage: 'publish',
        status: 'completed',
        summary: '已发布最终回答',
      },
      {
        event: 'agent_stage',
        run_id: 'run-complete',
        stage: 'tool',
        status: 'started',
        summary: '不应重新显示为执行中',
      },
    ]);

    const reconciled = reconcileTerminalStageEvents(events);
    expect(reconciled).toHaveLength(1);
    expect(reasoningStatusLabel(false, latestAgentStageEvent(reconciled))).toBe('完成');
  });

  it('bounds opaque event details before they enter the renderer', () => {
    const [event] = agentStageEvents([{
      event: 'agent_stage',
      run_id: 'run-large',
      stage: 'model',
      status: 'completed',
      summary: '模型请求原子操作',
      details: { output: 'x'.repeat(10_000) },
    }]);

    expect(event?.details?.output).toHaveLength(2_401);
    expect(JSON.stringify(event?.details).length).toBeLessThan(3_000);
  });

  it('retains historical events as read-only display data without routing new work', () => {
    const event = latestAgentStageEvent([{
      event: 'agent_stage_v2',
      run_id: 'legacy-run',
      stage: 'execution',
      status: 'completed',
      task_id: 'legacy-task',
      summary: '历史记录',
    }]);

    expect(event?.actionId).toBe('legacy-task');
    expect(event?.event).toBe('agent_stage_v2');
  });
});
