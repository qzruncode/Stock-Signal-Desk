import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMessage } from '@assistant-ui/react';
import type { AgentExecutionTrace } from '../../api/agent';
import { TeamBoard, TeamCollaborationView, TeamReviewCard } from './TeamBoard';
import { RuntimeErrorLine } from './TeamBoardLanes';
import type { TeamBoardModel, TeamModelProjection } from './TeamBoardUtils';

vi.mock('@assistant-ui/react', () => ({
  useMessage: vi.fn(),
}));

const mockMessage = (message: Record<string, unknown>) => {
  vi.mocked(useMessage).mockImplementation(((selector: (state: typeof message) => unknown) => (
    selector(message)
  )) as never);
};

const teamTrace = (overrides: Record<string, unknown> = {}) => ({
  team: {
    team_id: 'team-board-test',
    plan: {
      tasks: [
        { task_id: 'market-task', agent_id: 'market' },
        { task_id: 'fundamental-task', agent_id: 'fundamental' },
        { task_id: 'news-task', agent_id: 'news' },
      ],
    },
    ...overrides,
  },
}) as unknown as AgentExecutionTrace;

const workerStage = (role: string, taskId: string, status = 'started') => ({
  event: 'agent_stage',
  run_id: 'team-board-test',
  stage: 'planning',
  status,
  action_id: `team-board-test:${role}:${taskId}:worker`,
  details: {
    team_id: 'team-board-test',
    task_id: taskId,
    agent_id: `team-board-test:${role}:${taskId}`,
    expert_id: role,
  },
});

const reviewStage = (actionId: string, summary: string) => ({
  event: 'agent_stage',
  run_id: 'team-board-test',
  stage: 'reflection',
  status: 'completed',
  action_id: actionId,
  summary,
  details: { team_id: 'team-board-test' },
});

describe('TeamBoard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows persisted camelcase recovery receipts with their original error and call chain', () => {
    render(<RuntimeErrorLine event={{
      event: 'agent_stage', runId: 'run', stage: 'runtime_error', status: 'failed',
      details: { runtime_error: {
        errorCode: 'provider_timeout', exceptionType: 'TimeoutError', message: '上游请求超时',
        fallbackStatus: 'completed', fallbackCallIds: ['market:search', 'market:read'],
        node: 'atomic_tool_executor', phase: 'tool', attempt: 2,
      } },
    }} />);
    expect(screen.getByText('网页恢复：已返回，待核验覆盖')).toBeInTheDocument();
    expect(screen.getByText('原因：上游请求超时')).toBeInTheDocument();
    expect(screen.getByText('位置：atomic_tool_executor / tool')).toBeInTheDocument();
    expect(screen.getByText('调用尝试：2 次')).toBeInTheDocument();
    expect(screen.getByText('恢复调用：market:search、market:read')).toBeInTheDocument();
  });

  it('keeps one preparation state before the first Team plan event', () => {
    mockMessage({
      id: 'message-preparing',
      status: { type: 'running' },
      content: [],
      metadata: { unstable_data: [], custom: {} },
    });

    render(<TeamBoard selectedTeam />);

    expect(screen.getByRole('region', { name: 'Team协作分析' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Team 协作分析 · 准备中');
    expect(screen.getByText('正在准备并行核验方向')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Team成员工作区' })).not.toBeInTheDocument();
  });

  it('shows independent workers while collapsing tools and future review phases', () => {
    const trace = teamTrace();
    mockMessage({
      id: 'message-running',
      status: { type: 'running' },
      content: [
        {
          type: 'tool-call',
          tool_call_id: 'team-board-test:market:market-task:quote',
          tool_name: 'read_realtime_quote',
        },
      ],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task'),
          workerStage('fundamental', 'fundamental-task'),
          workerStage('news', 'news-task'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamBoard />);

    expect(screen.getByRole('status')).toHaveTextContent('Team 协作分析 · 执行中');
    expect(screen.getByRole('status')).toHaveTextContent('0/3 个方向已返回，3 个方向处理中');
    expect(screen.getByRole('region', { name: '行情分析工作区' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '基本面分析工作区' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '新闻分析工作区' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开行情分析工作区' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByLabelText('行情分析状态说明')).not.toBeInTheDocument();
    expect(screen.queryByText('等待该方向开始核验。')).not.toBeInTheDocument();
    document.querySelectorAll('[data-team-member]').forEach((member) => {
      expect(member).not.toHaveClass('rounded-xl');
      expect(member).not.toHaveClass('border');
      expect(member).not.toHaveClass('shadow-sm');
    });
    // No review phase has emitted a server lifecycle event yet.  The UI must
    // not manufacture a static review card or a permanently queued checklist.
    expect(screen.queryByText('等待成员核验完成后开始综合审查')).not.toBeInTheDocument();
    expect(screen.queryByText('后续 7 项')).not.toBeInTheDocument();
    expect(screen.queryByText('冲突检查')).not.toBeInTheDocument();
    expect(document.querySelectorAll('[data-team-child-tool]')).toHaveLength(0);
  });

  it('keeps terminal Team details collapsed and exposes the final summary trigger', () => {
    const trace = teamTrace({
      status: 'partial',
      results: [
        { task_id: 'market-task', agent_id: 'market', status: 'partial', summary: '行情方向部分完成。' },
        { task_id: 'fundamental-task', agent_id: 'fundamental', status: 'completed', summary: '基本面方向已完成。' },
        { task_id: 'news-task', agent_id: 'news', status: 'completed', summary: '新闻方向已完成。' },
      ],
      evidence_merge_status: 'partial',
      evidence_merge: { summary: '证据合并存在缺口。' },
    });
    mockMessage({
      id: 'message-terminal',
      status: { type: 'complete' },
      content: [],
      metadata: { unstable_data: [], custom: { agent_execution_trace: trace } },
    });

    render(<TeamBoard />);

    expect(screen.getByRole('button', { name: /展开Team 协作分析 · 部分完成/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByLabelText('Team成员工作区', { selector: 'div' })).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByRole('button', { name: /Team 协作分析 · 部分完成/ })).toHaveTextContent('部分完成');
  });

  it('does not show a failed worker summary that contradicts retained tool records', async () => {
    const trace = teamTrace({
      status: 'partial',
      results: [
        {
          task_id: 'market-task',
          agent_id: 'market',
          status: 'failed',
          summary: '本 worker 尚未开始执行。未调用任何允许的工具。',
        },
      ],
    });
    mockMessage({
      id: 'message-failed-worker-summary',
      status: { type: 'complete' },
      content: [{
        type: 'data',
        name: 'team-model-projection',
        part_id: 'failed-worker-report',
        data: {
          projection_source: 'model',
          scope: 'expert',
          task_id: 'market-task',
          agent_id: 'team-board-test:market:market-task',
          phase: 'worker',
          kind: 'report',
          text: '本 worker 尚未开始执行。未调用任何允许的工具。',
          sequence: 1,
        },
      }, {
        type: 'tool-call',
        tool_call_id: 'team-board-test:market:market-task:quote',
        tool_name: 'read_realtime_quote',
        result: { success: true },
      }],
      metadata: {
        unstable_data: [{
          event: 'agent_stage',
          run_id: 'team-board-test',
          stage: 'runtime_error',
          status: 'failed',
          action_id: 'team-board-test:market:market-task:worker',
          error_code: 'team_worker_timeout',
          details: {
            team_id: 'team-board-test',
            task_id: 'market-task',
            agent_id: 'team-board-test:market:market-task',
            expert_id: 'market',
          },
        }],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '展开行情分析工作区' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: '展开行情分析工作区' }));
    expect(screen.queryByText('本 worker 尚未开始执行。未调用任何允许的工具。')).not.toBeInTheDocument();
  });

  it('does not render a worker as completed when its result carries a timeout receipt', () => {
    const trace = teamTrace({
      status: 'completed',
      results: [
        {
          task_id: 'market-task',
          agent_id: 'market',
          status: 'completed',
          error_code: 'team_worker_timeout',
          summary: '已保留工具结果，但交接没有完成。',
        },
      ],
    });
    mockMessage({
      id: 'message-worker-timeout-receipt',
      status: { type: 'complete' },
      content: [],
      metadata: {
        unstable_data: [{
          event: 'agent_stage',
          run_id: 'team-board-test',
          stage: 'runtime_error',
          status: 'failed',
          error_code: 'team_worker_timeout',
          action_id: 'team-board-test:market:market-task:worker',
          details: {
            team_id: 'team-board-test',
            task_id: 'market-task',
            agent_id: 'team-board-test:market:market-task',
            expert_id: 'market',
          },
        }],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    expect(screen.getByRole('button', { name: '展开行情分析工作区' })).toHaveTextContent('部分完成');
    expect(screen.getByRole('button', { name: '展开行情分析工作区' })).not.toHaveTextContent('已完成');
  });

  it('renders a visible terminal receipt when the Team plan is rejected before dispatch', () => {
    const trace = teamTrace({
      status: 'blocked',
      plan: null,
      planSource: 'rejected',
      planError: 'team plan may require 64 tool calls, above max_tool_calls=32',
      failure: {
        status: 'blocked',
        errorCode: 'team_plan_rejected',
        detail: 'team plan may require 64 tool calls, above max_tool_calls=32',
        phase: 'plan_rejected',
        dispatchStatus: 'not_started',
      },
    });
    mockMessage({
      id: 'message-team-plan-rejected',
      status: { type: 'complete' },
      content: [],
      metadata: { unstable_data: [], custom: { agent_execution_trace: trace } },
    });

    render(<TeamCollaborationView />);

    expect(screen.getByRole('status', { name: 'Team 协作已阻塞' })).toHaveTextContent('未分发专家任务');
    expect(screen.getByRole('status', { name: 'Team 协作已阻塞' })).toHaveTextContent('协作计划未通过服务端边界校验');
    expect(screen.queryByText('协作准备中')).not.toBeInTheDocument();
    expect(document.querySelector('[data-team-failure-detail]')).toHaveTextContent('64 tool calls');
    expect(document.querySelector('[data-team-child-group]')).not.toBeInTheDocument();
  });

  it('supports a compact summary layout when member cards are rendered at process anchors', () => {
    const trace = teamTrace({
      status: 'completed',
      results: [
        { task_id: 'market-task', agent_id: 'market', status: 'completed', summary: '行情方向已完成。' },
        { task_id: 'fundamental-task', agent_id: 'fundamental', status: 'completed', summary: '基本面方向已完成。' },
        { task_id: 'news-task', agent_id: 'news', status: 'completed', summary: '新闻方向已完成。' },
      ],
    });
    mockMessage({
      id: 'message-summary',
      status: { type: 'complete' },
      content: [],
      metadata: { unstable_data: [], custom: { agent_execution_trace: trace } },
    });

    render(<TeamBoard layout="summary" />);

    expect(screen.getByRole('region', { name: 'Team协作分析' })).toHaveAttribute('data-team-board-layout', 'summary');
    expect(screen.getByText(/Team 协作分析 · 已完成/)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '行情分析工作区' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '基本面分析工作区' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '新闻分析工作区' })).not.toBeInTheDocument();
  });

  it('keeps terminal review details collapsed until the user opens them', async () => {
    const model: TeamBoardModel = {
      isTeam: true,
      teamId: 'team-review-test',
      status: 'partial',
      members: [],
      review: [
        {
          key: 'conflict',
          label: '冲突检查',
          status: 'partial',
          summary: '检测到一个需要保留的风险缺口。',
        },
      ],
      coordinatorProgress: [],
      unassignedTools: [],
      unassignedCharts: [],
      totalToolCount: 0,
      problem: true,
      failure: null,
    };

    render(<TeamReviewCard model={model} active={false} />);

    const trigger = screen.getByRole('button', { name: '展开综合审查' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('region', { name: '综合审查详情' })).not.toBeInTheDocument();

    fireEvent.click(trigger);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '收起综合审查' })).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByRole('region', { name: '综合审查详情' })).toHaveTextContent('检测到一个需要保留的风险缺口。');
    });
  });

  it('does not label review as running just because another Team lane is active', () => {
    const model: TeamBoardModel = {
      isTeam: true,
      teamId: 'team-review-active-worker',
      status: 'running',
      members: [],
      review: [
        {
          key: 'conflict',
          label: '冲突检查',
          status: 'completed',
          summary: '上一轮冲突检查已完成。',
        },
      ],
      coordinatorProgress: [],
      unassignedTools: [],
      unassignedCharts: [],
      totalToolCount: 0,
      problem: false,
      failure: null,
    };

    render(<TeamReviewCard model={model} active />);

    expect(screen.getByRole('button', { name: '展开综合审查' })).toHaveTextContent('1/1 项复核已完成');
    expect(screen.queryByText('执行中')).not.toBeInTheDocument();
  });

  it('owns handoff reports only inside the review disclosure, including replay', () => {
    const report = {
      type: 'data', name: 'team-review-report', part_id: 'team-board-test:review-report:0',
      data: {
        scope: 'review', report_source: 'handoff', title: '研究交接与核验记录',
        blocks: [{ section: '行情核验', content: '已交接观察：仅在综合审查中展示。' }],
      },
    };
    mockMessage({
      id: 'review-report-message', status: { type: 'complete' },
      content: [report, report],
      metadata: { unstable_data: [], custom: { agent_execution_trace: teamTrace({ status: 'completed' }) } },
    });
    const { container } = render(<TeamCollaborationView />);
    expect(screen.queryByText('研究交接与核验记录')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开综合审查' }));
    expect(screen.getByRole('region', { name: '综合审查详情' })).toHaveTextContent('已交接观察：仅在综合审查中展示。');
    expect(container.querySelectorAll('[data-team-review-report]')).toHaveLength(1);
    expect(container.querySelector('[data-team-review-report]')?.closest('[data-team-review-lane]')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '收起综合审查' }));
    expect(screen.queryByText('研究交接与核验记录')).not.toBeInTheDocument();
  });

  it('keeps review model projections inside the collapsed review lane', async () => {
    const model: TeamBoardModel = {
      isTeam: true,
      teamId: 'team-review-projection',
      status: 'completed',
      members: [],
      review: [
        {
          key: 'critic',
          label: '批评复核',
          status: 'completed',
          summary: '复核已完成。',
        },
      ],
      coordinatorProgress: [],
      unassignedTools: [],
      unassignedCharts: [],
      totalToolCount: 0,
      problem: false,
      failure: null,
    };
    const projection: TeamModelProjection = {
      partId: 'review-projection-1',
      contentIndex: 3,
      text: '综合审查发现一个需要补充的证据缺口。',
      projectionSource: 'model',
      scope: 'review',
      namespace: 'team-review-projection/review',
      agentId: 'team-review-projection:reviewer',
      taskId: 'review-task',
      phase: 'critic',
      kind: 'critic',
      sequence: 3,
    };

    const { container } = render(
      <TeamReviewCard model={model} active={false} projections={[projection]} />,
    );

    expect(screen.getByRole('button', { name: '展开综合审查' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(projection.text)).not.toBeInTheDocument();
    const lane = container.querySelector('[data-team-review-lane]');
    expect(lane).not.toHaveClass('rounded-xl');
    expect(lane).not.toHaveClass('border');

    fireEvent.click(screen.getByRole('button', { name: '展开综合审查' }));

    await waitFor(() => {
      const details = screen.getByRole('region', { name: '综合审查详情' });
      expect(details).toHaveTextContent(projection.text);
      expect(details.querySelector('[data-team-review-projection]')).toBeInTheDocument();
    });
  });

  it('renders worker status in one compact child group, never as fabricated assistant prose', () => {
    const trace = teamTrace();
    mockMessage({
      id: 'message-lanes-no-projection',
      status: { type: 'running' },
      content: [],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task'),
          workerStage('fundamental', 'fundamental-task'),
          workerStage('news', 'news-task'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    expect(document.querySelectorAll('[data-team-main-timeline]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-team-child-group]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-team-child-member]')).toHaveLength(3);
    expect(document.querySelectorAll('[data-team-expert-lane]')).toHaveLength(0);
    expect(screen.queryByText('行情方向开始核验')).not.toBeInTheDocument();
    expect(document.querySelector('[data-assistant-typing-indicator]')).toBeInTheDocument();
    expect(screen.queryByText('我现在开始核验行情方向的信息。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开行情分析工作区' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: '展开基本面分析工作区' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: '展开新闻分析工作区' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('does not turn a coordinator lifecycle event into a user-facing card', () => {
    mockMessage({
      id: 'message-coordinator-planning',
      status: { type: 'running' },
      content: [],
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'coordinator-planning',
            scope: 'coordinator',
            stage: 'planning',
            status: 'started',
            summary: '正在生成并行领域任务计划',
          },
        ],
        custom: {},
      },
    });

    render(<TeamCollaborationView />);

    expect(screen.queryByRole('status', { name: 'Team协调器状态' })).not.toBeInTheDocument();
    expect(screen.queryByText('正在生成并行领域任务计划')).not.toBeInTheDocument();
    const loading = screen.getByRole('status', { name: '主 Agent 回复生成中' });
    expect(loading).toHaveClass('justify-start');
    expect(loading.querySelector('.animate-spin')).not.toBeInTheDocument();
    const typingIndicator = loading.querySelector('[data-assistant-typing-dots]');
    expect(typingIndicator).toHaveClass('inline-flex');
    expect(typingIndicator).not.toHaveClass('rounded-full');
    expect(typingIndicator).not.toHaveClass('shadow-sm');
    expect(typingIndicator?.querySelectorAll('[data-assistant-typing-dot]')).toHaveLength(3);
  });

  it('keeps the main model narrative free of timeline rails and markers', () => {
    const trace = teamTrace();
    mockMessage({
      id: 'message-main-projection-style',
      status: { type: 'running' },
      content: [{
        type: 'data',
        name: 'team-model-projection',
        part_id: 'main-projection',
        data: {
          projection_source: 'model',
          scope: 'coordinator',
          text: '主 Agent 正在说明本次协作分工。',
          sequence: 1,
        },
      }],
      metadata: { unstable_data: [], custom: { agent_execution_trace: trace } },
    });

    render(<TeamCollaborationView />);

    const main = screen.getByRole('region', { name: 'Team主 Agent 时间线' });
    const projection = document.querySelector('[data-team-main-projection="coordinator"]');
    expect(main).not.toHaveClass('border-l');
    expect(projection).toHaveClass('min-w-0');
    expect(projection).not.toHaveClass('pl-3.5');
    expect(projection?.querySelector('[aria-hidden="true"]')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: '主 Agent 回复生成中' })).toBeInTheDocument();
  });

  it('keeps the main Agent execution state while Review is complete and the final answer is pending', async () => {
    const trace = teamTrace({
      status: 'completed',
      conflict_status: 'completed',
      conflict: { summary: '冲突检测已完成。' },
      critic_status: 'completed',
      critic: { summary: '独立复核已完成。' },
    });
    mockMessage({
      id: 'message-review-complete-answer-pending',
      status: { type: 'running' },
      content: [{
        type: 'data',
        name: 'team-model-projection',
        part_id: 'review-complete-coordinator',
        data: {
          projection_source: 'model',
          scope: 'coordinator',
          text: '三个方向已返回，我正在整理综合结论。',
          sequence: 1,
        },
      }],
      metadata: {
        unstable_data: [
          reviewStage('team-board-test:conflict-detector', '冲突检测已完成。'),
          reviewStage('team-board-test:critic-reviewer', '独立复核已完成。'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '展开综合审查' })).toHaveTextContent('已完成');
    }, { timeout: 2_500 });
    const loading = screen.getByRole('status', { name: '主 Agent 回复生成中' });
    expect(loading).toBeInTheDocument();
    expect(loading.querySelectorAll('[data-assistant-typing-dot]')).toHaveLength(3);
  });

  it('keeps the main narrative chronological while nesting each child flow at the delegation point', async () => {
    const trace = teamTrace({
      plan: {
        tasks: [
          { task_id: 'market-task', agent_id: 'market', allowed_tools: ['read_realtime_quote'] },
          { task_id: 'fundamental-task', agent_id: 'fundamental' },
          { task_id: 'news-task', agent_id: 'news' },
        ],
      },
    });
    mockMessage({
      id: 'message-lanes-projection',
      status: { type: 'running' },
      content: [
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-coordinator',
          data: {
            projection_source: 'model',
            scope: 'coordinator',
            text: '模型说明本次协作的实际分工。',
            sequence: 1,
          },
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-market-1',
          data: {
            projection_source: 'model',
            scope: 'expert',
            task_id: 'market-task',
            agent_id: 'team-board-test:market:market-task',
            text: '行情专家正在根据模型选择的工具核验价格和走势。',
            sequence: 2,
          },
        },
        {
          type: 'tool-call',
          tool_call_id: 'team-board-test:market:market-task:quote',
          tool_name: 'read_realtime_quote',
          args: {},
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-market-2',
          data: {
            projection_source: 'model',
            scope: 'expert',
            task_id: 'market-task',
            agent_id: 'team-board-test:market:market-task',
            text: '行情专家已经形成阶段性观察。',
            sequence: 3,
          },
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-news-1',
          data: {
            projection_source: 'model',
            scope: 'expert',
            task_id: 'news-task',
            agent_id: 'team-board-test:news:news-task',
            text: '新闻专家正在核对近期事件来源。',
            sequence: 4,
          },
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-handoff',
          data: {
            projection_source: 'model',
            scope: 'coordinator',
            text: '主协调器已收到专家交接，正在继续汇总。',
            sequence: 5,
          },
        },
      ],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task'),
          workerStage('fundamental', 'fundamental-task'),
          workerStage('news', 'news-task'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    await waitFor(() => {
      const main = screen.getByRole('region', { name: 'Team主 Agent 时间线' });
      expect(main).toHaveTextContent('模型说明本次协作的实际分工。');
      expect(main).toHaveTextContent('主协调器已收到专家交接，正在继续汇总。');
      expect(main).not.toHaveTextContent('行情专家正在根据模型选择的工具核验价格和走势。');
      expect(main).not.toHaveTextContent('新闻专家正在核对近期事件来源。');
      expect(document.querySelectorAll('[data-team-main-child-anchor]')).toHaveLength(1);
      expect(document.querySelectorAll('[data-team-child-group]')).toHaveLength(1);
      expect(document.querySelectorAll('[data-team-child-member]')).toHaveLength(3);
      expect(document.querySelectorAll('[data-team-expert-lane]')).toHaveLength(0);
      expect(screen.getByRole('button', { name: '展开行情分析工作区' })).toHaveAttribute('aria-expanded', 'false');
      const childGroup = document.querySelector('[data-team-child-group]');
      expect(childGroup).not.toHaveClass('rounded-xl');
      expect(childGroup).not.toHaveClass('border');
      expect(childGroup).not.toHaveClass('shadow-sm');
    }, { timeout: 2_500 });
    fireEvent.click(screen.getByRole('button', { name: '展开行情分析工作区' }));
    expect(screen.queryByText('行情专家正在根据模型选择的工具核验价格和走势。')).not.toBeInTheDocument();
    expect(document.querySelectorAll('[data-team-child-tool]')).toHaveLength(0);
    await waitFor(() => {
      expect(screen.getByText('行情专家正在根据模型选择的工具核验价格和走势。')).toBeInTheDocument();
      expect(document.querySelectorAll('[data-team-child-tool]')).toHaveLength(1);
      expect(screen.queryByText('行情方向开始核验')).not.toBeInTheDocument();
    }, { timeout: 2_500 });
  });

  it('waits for the first coordinator projection to finish before showing child execution', async () => {
    const trace = teamTrace();
    mockMessage({
      id: 'message-child-display-gate',
      status: { type: 'running' },
      content: [
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-before-dispatch',
          data: {
            projection_source: 'model',
            scope: 'coordinator',
            phase: 'planning',
            text: '我已经确定需要三个方向并行核验，现在开始分发任务。',
            sequence: 1,
          },
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-market-running',
          data: {
            projection_source: 'model',
            scope: 'expert',
            task_id: 'market-task',
            agent_id: 'team-board-test:market:market-task',
            text: '行情专家正在执行核验。',
            sequence: 2,
          },
        },
      ],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task'),
          workerStage('fundamental', 'fundamental-task'),
          workerStage('news', 'news-task'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    expect(document.querySelector('[data-team-child-group]')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: '主 Agent 回复生成中' })).toBeInTheDocument();

    await waitFor(() => {
      expect(document.querySelector('[data-team-child-group]')).toBeInTheDocument();
    }, { timeout: 2_500 });
  });

  it('keeps Review after the coordinator handoff when that projection arrives after child parts', () => {
    const trace = teamTrace({ status: 'completed' });
    mockMessage({
      id: 'message-review-anchor',
      status: { type: 'complete' },
      content: [
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-initial',
          data: {
            projection_source: 'model',
            scope: 'coordinator',
            phase: 'planning',
            text: '先完成三个方向的独立核验。',
            sequence: 1,
          },
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-worker',
          data: {
            projection_source: 'model',
            scope: 'expert',
            task_id: 'market-task',
            agent_id: 'team-board-test:market:market-task',
            text: '行情方向已经返回阶段性观察。',
            sequence: 2,
          },
        },
        {
          type: 'data',
          name: 'team-model-projection',
          part_id: 'projection-handoff-after-child',
          data: {
            projection_source: 'model',
            scope: 'coordinator',
            phase: 'handoff',
            kind: 'handoff',
            text: '三个方向已经返回，我现在进入综合审查。',
            sequence: 3,
          },
        },
      ],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task', 'completed'),
          workerStage('fundamental', 'fundamental-task', 'completed'),
          workerStage('news', 'news-task', 'completed'),
          reviewStage('team-board-test:conflict-detector', '冲突检查已完成。'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    const timeline = screen.getByRole('region', { name: 'Team主 Agent 时间线' });
    const children = Array.from(timeline.children);
    const childAnchor = timeline.querySelector('[data-team-main-child-anchor]');
    const reviewAnchor = timeline.querySelector('[data-team-main-review-anchor]');
    const handoffProjection = timeline.querySelector('[data-team-projection-id="projection-handoff-after-child"]');

    expect(childAnchor).toBeInTheDocument();
    expect(handoffProjection).toBeInTheDocument();
    expect(reviewAnchor).toBeInTheDocument();
    expect(children.indexOf(childAnchor as Element)).toBeLessThan(children.indexOf(handoffProjection as Element));
    expect(children.indexOf(handoffProjection as Element)).toBeLessThan(children.indexOf(reviewAnchor as Element));
  });

  it('shows the review lane only after a real review event or model projection exists', () => {
    const trace = teamTrace();
    mockMessage({
      id: 'message-review-gated',
      status: { type: 'running' },
      content: [],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task'),
          workerStage('fundamental', 'fundamental-task'),
          workerStage('news', 'news-task'),
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    const { unmount } = render(<TeamCollaborationView />);
    expect(document.querySelector('[data-team-main-review-anchor]')).not.toBeInTheDocument();
    unmount();

    mockMessage({
      id: 'message-review-started',
      status: { type: 'running' },
      content: [],
      metadata: {
        unstable_data: [
          workerStage('market', 'market-task'),
          workerStage('fundamental', 'fundamental-task'),
          workerStage('news', 'news-task'),
          {
            event: 'agent_stage',
            run_id: 'team-board-test',
            stage: 'reflection',
            status: 'started',
            action_id: 'team-board-test:reviewer:conflict-detector',
            details: {
              team_id: 'team-board-test',
              task_id: 'review-task',
              agent_id: 'team-board-test:reviewer:conflict-detector',
              expert_id: 'reviewer',
            },
          },
        ],
        custom: { agent_execution_trace: trace },
      },
    });

    render(<TeamCollaborationView />);

    expect(document.querySelector('[data-team-main-review-anchor]')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Team综合审查' })).toHaveTextContent('综合审查');
  });
});
