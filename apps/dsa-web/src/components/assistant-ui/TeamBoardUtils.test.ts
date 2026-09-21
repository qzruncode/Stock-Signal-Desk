import { describe, expect, it } from 'vitest';
import type { AgentExecutionTrace } from '../../api/agent';
import { toCamelCase } from '../../api/utils';
import {
  buildTeamBoardModel,
  teamModelProjectionsFromParts,
  teamMemberForProgressAnchor,
  teamWorkerProgressAnchors,
} from './TeamBoardUtils';

const stage = (overrides: Record<string, unknown> = {}) => ({
  event: 'agent_stage',
  run_id: 'team-run',
  stage: 'tool',
  status: 'completed',
  action_id: 'team-1:market:market-task:call-market',
  tool_call_id: 'team-1:market:market-task:call-market',
  summary: '读取行情完成',
  details: {
    team_id: 'team-1',
    task_id: 'market-task',
    agent_id: 'team-1:market:market-task',
    expert_id: 'market',
  },
  ...overrides,
});

const trace = {
  team: {
    team_id: 'team-1',
    status: 'partial',
    plan: {
      tasks: [
        { task_id: 'market-task', agent_id: 'market', allowed_tools: ['read_realtime_quote'] },
        { task_id: 'news-task', agent_id: 'news', allowed_tools: ['read_company_news'] },
      ],
    },
    results: [
      {
        task_id: 'market-task',
        agent_id: 'team-1:market:market-task',
        expert_id: 'market',
        status: 'completed',
        summary: '行情方向已完成。',
      },
      {
        task_id: 'news-task',
        agent_id: 'team-1:news:news-task',
        expert_id: 'news',
        status: 'partial',
        summary: '新闻方向部分完成。',
      },
    ],
  },
} as unknown as AgentExecutionTrace;

describe('buildTeamBoardModel', () => {
  it('retains recovered tool errors without downgrading a completed worker', () => {
    const model = buildTeamBoardModel([], [
      stage({ status: 'failed', error_code: 'DataNotReady' }),
      stage({ stage: 'planning', action_id: 'team-1:market:market-task:worker', status: 'completed' }),
    ], trace, false);
    expect(model.members.find((member) => member.role === 'market')?.status).toBe('completed');
  });

  it('projects a live partial report from event details before trace persistence', () => {
    const model = buildTeamBoardModel([], [stage({
      stage: 'planning', status: 'failed', action_id: 'team-1:market:market-task:worker',
      details: { team_id: 'team-1', task_id: 'market-task', expert_id: 'market', status: 'partial' },
    })], undefined, true);
    expect(model.members.find((member) => member.role === 'market')?.status).toBe('partial');
  });

  it('coalesces streamed model projection updates into one narrative item', () => {
    const projections = teamModelProjectionsFromParts([
      {
        type: 'data',
        name: 'team-model-projection',
        data: {
          projection_source: 'model',
          projection_id: 'team-1:plan:projection',
          scope: 'coordinator',
          text: '我先选择行情',
          sequence: 1,
        },
      },
      {
        type: 'data',
        name: 'team-model-projection',
        data: {
          projection_source: 'model',
          projection_id: 'team-1:plan:projection',
          scope: 'coordinator',
          text: '我先选择行情和基本面专家并行核验。',
          sequence: 2,
        },
      },
    ]);

    expect(projections).toHaveLength(1);
    expect(projections[0]).toEqual(expect.objectContaining({
      partId: 'team-1:plan:projection',
      contentIndex: 0,
      text: '我先选择行情和基本面专家并行核验。',
    }));
  });

  it('does not replace a richer projection with a shorter accepted update', () => {
    const projections = teamModelProjectionsFromParts([
      {
        type: 'data',
        name: 'team-model-projection',
        data: {
          projection_source: 'model',
          projection_id: 'team-1:coordinator:handoff',
          scope: 'coordinator',
          text: '我已经根据用户的问题完成了三个独立方向的任务拆分，现在开始并行核验。',
          sequence: 10,
        },
      },
      {
        type: 'data',
        name: 'team-model-projection',
        data: {
          projection_source: 'model',
          projection_id: 'team-1:coordinator:handoff',
          scope: 'coordinator',
          text: '已开始并行核验。',
          sequence: 11,
        },
      },
    ]);

    expect(projections).toHaveLength(1);
    expect(projections[0].text).toBe(
      '我已经根据用户的问题完成了三个独立方向的任务拆分，现在开始并行核验。',
    );
  });

  it('deduplicates an accepted contract projection replayed with a new part id', () => {
    const projections = teamModelProjectionsFromParts([
      {
        type: 'data',
        name: 'team-model-projection',
        part_id: 'stream-part',
        data: {
          projection_source: 'model',
          scope: 'coordinator',
          namespace: 'team:1/coordinator',
          phase: 'handoff',
          kind: 'handoff',
          text: '仍有 1 个方向存在缺口。',
          sequence: 1,
        },
      },
      {
        type: 'data',
        name: 'team-model-projection',
        part_id: 'accepted-part',
        data: {
          projection_source: 'model',
          scope: 'coordinator',
          namespace: 'team:1/coordinator',
          phase: 'handoff',
          kind: 'handoff',
          text: '仍有 1 个方向存在缺口。',
          sequence: 2,
        },
      },
    ]);

    expect(projections).toHaveLength(1);
  });

  it('binds each worker start sentence to its own member card identity', () => {
    const anchors = teamWorkerProgressAnchors([
      stage({
        stage: 'planning',
        status: 'started',
        action_id: 'team-1:market:market-task:worker',
        details: {
          team_id: 'team-1',
          task_id: 'market-task',
          agent_id: 'team-1:market:market-task',
          expert_id: 'market',
          user_message: '我现在开始核验行情方向的信息。',
        },
      }),
    ]);
    const model = buildTeamBoardModel([], [], trace, true);

    expect(anchors).toEqual([
      expect.objectContaining({
        role: 'market',
        taskId: 'market-task',
        agentId: 'team-1:market:market-task',
        text: '我现在开始核验行情方向的信息。',
      }),
    ]);
    expect(teamMemberForProgressAnchor(model.members, anchors[0]!)).toEqual(
      expect.objectContaining({ key: 'market-task', role: 'market' }),
    );
  });

  it('keeps parallel worker tools in independent member workspaces', () => {
    const model = buildTeamBoardModel(
      [
        {
          type: 'tool-call',
          tool_call_id: 'team-1:market:market-task:call-market',
          tool_name: 'read_realtime_quote',
          result: { success: true },
        },
        {
          type: 'tool-call',
          tool_call_id: 'team-1:news:news-task:call-news',
          tool_name: 'read_company_news',
          result: { success: true },
        },
        {
          type: 'data',
          name: 'stock-chart',
          data: {
            tool_call_id: 'team-1:market:market-task:call-market',
            title: '行情走势',
            series: [{ key: 'price', label: '价格' }],
            data: [{ x: '2026-09-16', price: 11.21 }],
          },
        },
      ],
      [
        stage(),
        stage({
          action_id: 'team-1:news:news-task:call-news',
          tool_call_id: 'team-1:news:news-task:call-news',
          summary: '读取新闻完成',
          details: {
            team_id: 'team-1',
            task_id: 'news-task',
            agent_id: 'team-1:news:news-task',
            expert_id: 'news',
          },
        }),
      ],
      trace,
      false,
    );

    expect(model.isTeam).toBe(true);
    expect(model.members.map((member) => member.role)).toEqual(['market', 'news']);
    expect(model.members.find((member) => member.role === 'market')?.tools).toHaveLength(1);
    expect(model.members.find((member) => member.role === 'news')?.tools).toHaveLength(1);
    expect(model.members.find((member) => member.role === 'market')?.status).toBe('completed');
    expect(model.members.find((member) => member.role === 'news')?.status).toBe('partial');
    expect(model.members.find((member) => member.role === 'market')?.charts).toHaveLength(1);
    expect(model.unassignedTools).toHaveLength(0);
  });

  it('uses the model projection as the member status explanation', () => {
    const model = buildTeamBoardModel(
      [
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'team-1:market:market-task:worker:1:projection',
          data: {
            projection_source: 'model',
            scope: 'expert',
            task_id: 'market-task',
            agent_id: 'team-1:market:market-task:attempt-1',
            text: '模型说明行情方向已完成证据核验。',
            sequence: 1,
          },
        },
      ],
      [stage({
        stage: 'planning',
        status: 'started',
        action_id: 'team-1:market:market-task:worker',
        details: {
          team_id: 'team-1',
          task_id: 'market-task',
          agent_id: 'team-1:market:market-task:attempt-1',
          expert_id: 'market',
        },
      })],
      trace,
      true,
    );

    expect(model.members.find((member) => member.role === 'market')?.progress)
      .toBe('模型说明行情方向已完成证据核验。');
  });

  it('uses a newer live worker start instead of a stale persisted result', () => {
    const model = buildTeamBoardModel(
      [{
        type: 'data',
        name: 'agent-stage',
        data: {
          event: 'agent_stage',
          run_id: 'team-1',
          stage: 'planning',
          status: 'started',
          action_id: 'team-1:market:market-task:worker',
          sequence: 8,
          details: {
            team_id: 'team-1',
            task_id: 'market-task',
            agent_id: 'team-1:market:market-task',
            expert_id: 'market',
          },
        },
      }],
      [],
      trace,
      true,
    );

    expect(model.members.find((member) => member.role === 'market')?.status).toBe('running');
  });

  it('uses the durable result when a same-attempt worker start is stale', () => {
    const model = buildTeamBoardModel(
      [],
      [stage({
        stage: 'planning',
        status: 'started',
        action_id: 'team-1:market:market-task:worker',
        details: {
          team_id: 'team-1',
          task_id: 'market-task',
          agent_id: 'team-1:market:market-task',
          expert_id: 'market',
          attempt: 1,
        },
      })],
      toCamelCase<AgentExecutionTrace>({
        team: {
          team_id: 'team-1',
          status: 'partial',
          plan: { tasks: [{ task_id: 'market-task', agent_id: 'market' }] },
          results: [{
            task_id: 'market-task',
            agent_id: 'team-1:market:market-task',
            expert_id: 'market',
            status: 'completed',
            attempt: 1,
          }],
        },
      }),
      true,
    );

    expect(model.members.find((member) => member.role === 'market')?.status).toBe('completed');
  });

  it('uses a newer review lifecycle event instead of a stale completed review value', () => {
    const model = buildTeamBoardModel(
      [{
        type: 'data',
        name: 'agent-stage',
        data: {
          event: 'agent_stage',
          run_id: 'team-1',
          stage: 'planning',
          status: 'started',
          action_id: 'team-1:reviewer:conflict-detector',
          sequence: 9,
          details: {
            team_id: 'team-1',
            task_id: 'conflict',
            agent_id: 'team-1:reviewer:conflict-detector',
            expert_id: 'reviewer',
          },
        },
      }],
      [],
      {
        team: {
          team_id: 'team-1',
          conflict_status: 'completed',
          conflict: { summary: '上一轮冲突检查已完成。' },
        },
      } as unknown as AgentExecutionTrace,
      true,
    );

    expect(model.review.find((phase) => phase.key === 'conflict')).toEqual(
      expect.objectContaining({ status: 'running' }),
    );
  });

  it('renders a registered extension expert from collaboration state without a fixed role union', () => {
    const extensionTrace = toCamelCase<AgentExecutionTrace>({
      team: {
        team_id: 'team-extension',
        status: 'running',
        collaboration: {
          plan: {
            tasks: [
              {
                task_id: 'valuation-task',
                agent_id: 'valuation',
                agent_display_name: '估值分析',
                allowed_tools: ['read_valuation'],
              },
            ],
          },
          reports: {
            'valuation-task': {
              task_id: 'valuation-task',
              agent_id: 'valuation',
              agent_display_name: '估值分析',
              status: 'completed',
              summary: '估值方向已完成。',
            },
          },
        },
      },
    });
    const extensionEvent = stage({
      stage: 'planning',
      action_id: 'team-extension:valuation:valuation-task:worker',
      details: {
        team_id: 'team-extension',
        task_id: 'valuation-task',
        agent_id: 'team-extension:valuation:valuation-task',
        expert_id: 'valuation',
        agent_display_name: '估值分析',
      },
    });

    const model = buildTeamBoardModel([], [extensionEvent], extensionTrace, true);

    expect(model.members).toEqual([
      expect.objectContaining({
        role: 'valuation',
        label: '估值分析',
        taskId: 'valuation-task',
        status: 'completed',
      }),
    ]);
  });

  it('keeps common tools unassigned instead of attributing them to every member', () => {
    const model = buildTeamBoardModel(
      [{ type: 'tool-call', tool_call_id: 'common-call', tool_name: 'search_stocks' }],
      [stage({
        action_id: 'team-1:market:market-task:other-call',
        tool_call_id: 'team-1:market:market-task:other-call',
      })],
      {
        team: {
          team_id: 'team-1',
          plan: {
            tasks: [
              { task_id: 'market-task', agent_id: 'market', allowed_tools: ['search_stocks'] },
              { task_id: 'news-task', agent_id: 'news', allowed_tools: ['search_stocks'] },
            ],
          },
        },
      } as unknown as AgentExecutionTrace,
      true,
    );

    expect(model.unassignedTools).toHaveLength(1);
    expect(model.unassignedTools[0]?.part.tool_name).toBe('search_stocks');
  });

  it('keeps reviewer lifecycle events in review instead of creating member cards', () => {
    const model = buildTeamBoardModel(
      [],
      [
        stage({
          stage: 'planning',
          action_id: 'team-1:reviewer:evidence-merge',
          tool_call_id: undefined,
          details: {
            team_id: 'team-1',
            task_id: 'evidence-merge',
            agent_id: 'team-1:reviewer:evidence-merge',
            expert_id: 'reviewer',
          },
        }),
      ],
      trace,
      false,
    );

    expect(model.members.map((member) => member.role)).toEqual(['market', 'news']);
  });

  it('keeps partial worker results visible when terminal worker stages are failed', () => {
    const finalTrace = toCamelCase<AgentExecutionTrace>({
      team: {
        team_id: 'team-1',
        status: 'partial',
        plan: {
          tasks: [
            { task_id: 'market-task', agent_id: 'market' },
            { task_id: 'fundamental-task', agent_id: 'fundamental' },
            { task_id: 'news-task', agent_id: 'news' },
          ],
        },
        results: [
          { task_id: 'market-task', agent_id: 'market', status: 'partial', summary: '行情部分完成。' },
          { task_id: 'fundamental-task', agent_id: 'fundamental', status: 'partial', summary: '基本面部分完成。' },
          { task_id: 'news-task', agent_id: 'news', status: 'partial', summary: '新闻部分完成。' },
        ],
      },
    });
    const workerStage = (role: string, taskId: string) => stage({
      stage: 'planning',
      status: 'failed',
      action_id: `team-1:${role}:${taskId}:worker`,
      details: {
        team_id: 'team-1',
        task_id: taskId,
        agent_id: `team-1:${role}:${taskId}`,
        expert_id: role,
      },
    });

    const model = buildTeamBoardModel(
      [],
      [
        workerStage('market', 'market-task'),
        workerStage('fundamental', 'fundamental-task'),
        workerStage('news', 'news-task'),
      ],
      finalTrace,
      false,
    );

    expect(model.members.map((member) => member.status)).toEqual(['partial', 'partial', 'partial']);
  });

  it('does not turn a partial publish into a Team-level failure receipt', () => {
    const model = buildTeamBoardModel(
      [],
      [stage({
        stage: 'publish',
        status: 'failed',
        summary: '已发布带明确协作缺口的部分结果',
        error_code: 'evidence_link_incomplete',
        details: {
          team_id: 'team-1',
          status: 'partial',
          evidence_merge_status: 'partial',
        },
      })],
      {
        team: {
          team_id: 'team-1',
          status: 'partial',
          plan: { tasks: [{ task_id: 'market-task', agent_id: 'market' }] },
          results: [{ task_id: 'market-task', agent_id: 'market', status: 'completed' }],
        },
      } as unknown as AgentExecutionTrace,
      false,
    );

    expect(model.status).toBe('partial');
    expect(model.failure).toBeNull();
    expect(model.problem).toBe(true);
  });

  it('marks a post-dispatch Team failure as stopped instead of not started', () => {
    const model = buildTeamBoardModel(
      [],
      [stage({
        stage: 'publish',
        status: 'failed',
        summary: 'Team 汇总失败',
        error_code: 'team_synthesis_failed',
        details: { team_id: 'team-1' },
      })],
      {
        team: {
          team_id: 'team-1',
          status: 'failed',
          dispatch_round: 1,
          dispatched_task_ids: ['market-task'],
          plan: { tasks: [{ task_id: 'market-task', agent_id: 'market' }] },
        },
      } as unknown as AgentExecutionTrace,
      false,
    );

    expect(model.failure).toEqual(expect.objectContaining({
      status: 'failed',
      dispatchStatus: 'stopped',
    }));
  });

  it('projects camelCase review statuses and hides optional phases that never ran', () => {
    const replayedTrace = toCamelCase<AgentExecutionTrace>({
      team: {
        team_id: 'team-1',
        status: 'partial',
        evidence_merge_status: 'partial',
        conflict_status: 'completed',
        critic_status: 'completed',
        consensus_status: 'completed',
        criteria_status: 'blocked',
        evidence_merge: { summary: '证据合并存在缺口。' },
        conflict: { summary: '已完成冲突检查。' },
        critic: { summary: '已完成独立复核。' },
        consensus: { summary: '已形成部分共识。' },
        criteria_assessment: { summary: '目标检查未全部满足。' },
      },
    });

    const model = buildTeamBoardModel([], [], replayedTrace, false);

    expect(model.review.map((phase) => [phase.key, phase.status])).toEqual([
      ['evidence-merge', 'partial'],
      ['conflict', 'completed'],
      ['critic', 'completed'],
      ['consensus', 'completed'],
      ['criteria', 'blocked'],
    ]);
    expect(model.review.map((phase) => phase.summary)).toEqual([
      '证据合并存在缺口。',
      '已完成冲突检查。',
      '已完成独立复核。',
      '已形成部分共识。',
      '目标检查未全部满足。',
    ]);
  });

  it('projects review dispatch and gate only after their server events exist', () => {
    const model = buildTeamBoardModel(
      [],
      [
        stage({
          stage: 'reflection',
          action_id: 'team-1:review-dispatch',
          summary: '已并行启动复核',
          details: { team_id: 'team-1' },
        }),
        stage({
          stage: 'reflection',
          action_id: 'team-1:review-gate',
          summary: '复核已汇合',
          details: { team_id: 'team-1' },
        }),
      ],
      {
        team: {
          team_id: 'team-1',
          status: 'partial',
        },
      } as unknown as AgentExecutionTrace,
      false,
    );

    expect(model.review.map((phase) => [phase.key, phase.status])).toEqual([
      ['review-dispatch', 'completed'],
      ['review-gate', 'completed'],
    ]);
    expect(model.review.map((phase) => phase.summary)).toEqual([
      '已并行启动复核',
      '复核已汇合',
    ]);
  });

  it('does not show a future-stage clause under a terminal review status', () => {
    const model = buildTeamBoardModel(
      [],
      [
        stage({
          stage: 'reflection',
          action_id: 'team-1:conflict-detector',
          summary: '冲突检测完成，正在进入独立批评复核',
          details: { team_id: 'team-1' },
        }),
        stage({
          stage: 'reflection',
          action_id: 'team-1:critic-reviewer',
          summary: '独立批评复核完成，正在判断是否需要多空对抗审查',
          details: { team_id: 'team-1' },
        }),
      ],
      {
        team: {
          team_id: 'team-1',
          status: 'partial',
        },
      } as unknown as AgentExecutionTrace,
      false,
    );

    expect(model.review.map((phase) => phase.summary)).toEqual([
      '冲突检测完成',
      '独立批评复核完成',
    ]);
  });

  it('uses terminal reviewer events when legacy traces omit case statuses', () => {
    const legacyTrace = toCamelCase<AgentExecutionTrace>({
      team: {
        team_id: 'team-1',
        status: 'partial',
        bull_case: { stance: 'bull', summary: '看多审查没有足够证据。' },
        bear_case: { stance: 'bear', summary: '看空审查没有足够证据。' },
      },
    });
    const caseEvent = (stance: 'bull' | 'bear') => stage({
      stage: 'reflection',
      action_id: `team-1:${stance}-case-reviewer`,
      details: {
        team_id: 'team-1',
        reviewer: stance === 'bull' ? 'BullCaseReviewer' : 'BearCaseReviewer',
        stance,
      },
      summary: `${stance} case 完成`,
    });

    const model = buildTeamBoardModel([], [caseEvent('bull'), caseEvent('bear')], legacyTrace, false);

    expect(model.review.map((phase) => [phase.key, phase.status])).toEqual([
      ['bull-case', 'completed'],
      ['bear-case', 'completed'],
    ]);
  });

  it('does not show untriggered optional review phases as stuck in a terminal run', () => {
    const model = buildTeamBoardModel(
      [],
      [],
      {
        team: {
          team_id: 'team-1',
          status: 'completed',
          bull_case_status: 'not_started',
          bear_case_status: 'not_started',
        },
      } as unknown as AgentExecutionTrace,
      false,
    );

    expect(model.review).toEqual([]);
  });

  it('does not show default review objects and closes missing workers on cancellation', () => {
    const model = buildTeamBoardModel(
      [],
      [
        stage({
          stage: 'planning',
          status: 'started',
          action_id: 'team-1:market:market-task:worker',
          details: {
            team_id: 'team-1',
            task_id: 'market-task',
            agent_id: 'team-1:market:market-task',
            expert_id: 'market',
          },
        }),
        {
          event: 'agent_stage',
          run_id: 'team-run',
          stage: 'publish',
          status: 'cancelled',
          summary: '用户已停止本轮任务',
        },
      ],
      {
        team: {
          team_id: 'team-1',
          status: 'dispatching',
          plan: {
            tasks: [
              { task_id: 'market-task', agent_id: 'market' },
              { task_id: 'news-task', agent_id: 'news' },
            ],
          },
          evidence_merge_status: 'not_started',
          evidence_merge: { status: 'not_started' },
          review_dispatch: { status: 'not_started' },
          team_review_dispatch_status: 'not_started',
        },
      } as unknown as AgentExecutionTrace,
      false,
    );

    expect(model.status).toBe('cancelled');
    expect(model.members.find((member) => member.role === 'market')?.status).toBe('cancelled');
    expect(model.review).toEqual([]);
  });
});
