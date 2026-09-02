import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMessage, useMessageTiming } from '@assistant-ui/react';
import { AgentExecutionTimeline } from './AgentReasoning';

vi.mock('@assistant-ui/react', () => ({
  useMessage: vi.fn(),
  useMessageTiming: vi.fn(),
}));

const mockMessage = (message: Record<string, unknown>) => {
  vi.mocked(useMessage).mockImplementation(((selector: (state: typeof message) => unknown) => (
    selector(message)
  )) as never);
};

describe('AgentExecutionTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useMessageTiming).mockReturnValue(undefined);
  });

  it('merges the actual tool result into one compact execution line', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-tool',
            stage: 'model',
            status: 'completed',
            summary: '模型请求 1 个原子操作',
            details: {
              model_turn: 1,
              operations: [{
                tool_name: 'search_web_source',
                arguments: { source_id: 'exa', query: '人形机器人产业链' },
                argument_keys: ['query', 'source_id'],
              }],
            },
          },
          {
            event: 'agent_stage',
            run_id: 'run-tool',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-news',
            summary: 'search_web_source 已返回',
            details: { tool_name: 'search_web_source', evidence_id: 'ev_call-news' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-tool',
            stage: 'evidence',
            status: 'completed',
            summary: '已核对 1 条结论与 1 条成功证据',
            details: {
              evidence_ids: ['ev_call-news'],
              claim_count: 1,
              fact_claim_count: 1,
              inference_claim_count: 0,
            },
          },
          {
            event: 'agent_stage',
            run_id: 'run-tool',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [
              {
                action_id: 'call-news',
                tool_name: 'search_web_source',
                arguments: { source_id: 'exa', query: '人形机器人产业链', num_results: 2 },
                success: true,
                data_time: '2026-08-08',
                source_refs: ['https://example.test/news'],
                source_labels: ['Exa', '证券时报', '财联社'],
                result_count: 2,
                result_items: [
                  {
                    title: '人形机器人供应链进展',
                    url: 'https://example.test/robotics-1',
                    source: '证券时报',
                    published_at: '2026-08-08',
                    summary: '核心零部件进入放量阶段。',
                  },
                  {
                    title: '灵巧手产业观察',
                    url: 'https://example.test/robotics-2',
                    source: '财联社',
                    published_at: '2026-08-07',
                  },
                ],
              },
            ],
          },
        },
      },
    });

    const { container } = render(<AgentExecutionTimeline />);

    const toggle = screen.getByRole('button', { name: /展开执行过程，执行完成/ });
    const details = container.querySelector<HTMLElement>('[role="region"][aria-label="执行过程详情"]')!;
    expect(toggle).toBeInTheDocument();
    expect(details).toHaveAttribute('aria-hidden', 'true');
    expect(details).toHaveStyle({ gridTemplateRows: '0fr' });
    expect(details).toHaveClass('transition-[grid-template-rows]');
    fireEvent.click(toggle);
    expect(details).toHaveAttribute('aria-hidden', 'false');
    expect(details).toHaveStyle({ gridTemplateRows: '1fr' });
    expect(screen.getByText('执行过程')).toBeInTheDocument();
    expect(screen.getByText('search_web_source')).toBeInTheDocument();
    expect(screen.getByText(/请求：source_id=exa · query=人形机器人产业链 · num_results=2/)).toBeInTheDocument();
    expect(screen.getByText(/返回 2 条结果 · 数据来源：Exa、证券时报、财联社/)).toBeInTheDocument();
    expect(screen.getByText(/数据时间：2026-08-08/)).toBeInTheDocument();
    expect(screen.getByText(/结果：.*证据：ev_call-news/)).toBeInTheDocument();
    expect(screen.getByText(/1\. 人形机器人供应链进展 · 证券时报 · 2026-08-08/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://example.test/robotics-1' })).toBeInTheDocument();
    expect(screen.getByText(/2\. 灵巧手产业观察 · 财联社 · 2026-08-07/)).toBeInTheDocument();
    expect(screen.getByText(/1 条结论（事实 1、推断 0）/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /查看详细|收起详细|查看详情/ })).not.toBeInTheDocument();
  });

  it('shows a rejected operation as not executed rather than a runtime failure', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-rejected',
            stage: 'approval',
            status: 'completed',
            action_id: 'notify-1',
            summary: '用户拒绝执行 notify_user，该操作没有被调用',
            details: { tool_name: 'notify_user', arguments: { message: '测试通知' } },
          },
          {
            event: 'agent_stage',
            run_id: 'run-rejected',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [
              {
                action_id: 'notify-1',
                tool_name: 'notify_user',
                success: false,
                error_code: 'approval_rejected',
              },
            ],
          },
        },
      },
    });

    render(<AgentExecutionTimeline />);

    fireEvent.click(screen.getByRole('button', { name: /展开执行过程/ }));
    expect(screen.getByText('notify_user：用户拒绝，未执行')).toBeInTheDocument();
    expect(screen.queryByText(/调用未成功/)).not.toBeInTheDocument();
  });

  it('makes an evidence repair visible as a concrete gap instead of a generic stage', () => {
    const fullIssue = '带有明确时间口径的结论没有紧邻的 evidence_id；结论使用了当前或最新时间口径，但引用证据没有可用数据时间，必须保留完整说明供用户核对';
    mockMessage({
      status: { type: 'running' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-repair',
            stage: 'evidence',
            status: 'started',
            summary: '发现候选回答的证据关联缺口，正在请求模型基于已有证据修订',
            details: {
              issues: [fullIssue],
              available_evidence_ids: ['ev_a'],
            },
          },
        ],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline />);

    expect(screen.getByText('关联证据')).toBeInTheDocument();
    expect(screen.getByText(`缺口 1：${fullIssue}`)).toBeInTheDocument();
    expect(screen.getByText(`缺口 1：${fullIssue}`)).not.toHaveClass('truncate');
  });

  it('keeps the candidate-answer stage but does not duplicate its body', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-candidate',
            stage: 'model',
            status: 'completed',
            summary: '模型已给出候选回答，正在检查其证据关联',
            details: {
              model_turn: 2,
              answer_preview: '这段候选回答正文不应在执行过程重复展示',
              answer_character_count: 22,
            },
          },
          {
            event: 'agent_stage',
            run_id: 'run-candidate',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline />);

    fireEvent.click(screen.getByRole('button', { name: /展开执行过程/ }));
    expect(screen.getByText('模型决策 · 第 2 轮')).toBeInTheDocument();
    expect(screen.getByText('模型已给出候选回答，正在检查其证据关联')).toBeInTheDocument();
    expect(screen.queryByText(/候选回答正文不应/)).not.toBeInTheDocument();
    expect(screen.queryByText(/候选回答摘录/)).not.toBeInTheDocument();
  });

  it('keeps the latest stage summary visible without a collapsible header', () => {
    const summary = '第 1 轮：模型正在基于当前问题、工具观察和证据决定下一步';
    mockMessage({
      status: { type: 'running' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-summary',
            stage: 'model',
            status: 'started',
            summary,
            details: { model_turn: 1 },
          },
        ],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline />);

    const header = screen.getByRole('status');
    expect(header).not.toHaveTextContent(summary);
    expect(screen.getByText(summary)).toBeInTheDocument();
    expect(header.tagName).not.toBe('BUTTON');
    expect(screen.queryByRole('button', { name: /查看详细|收起详细|查看详情/ })).not.toBeInTheDocument();
  });

  it('lists exact historical references instead of calling their length a source count', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-old',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-old',
            summary: 'search_web_source 已返回结果',
            details: { tool_name: 'search_web_source' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-old',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [
              {
                action_id: 'call-old',
                tool_name: 'search_web_source',
                success: true,
                source_refs: [
                  'firecrawl_searxng',
                  'https://finance.example.test/article-1',
                  'finance.example.test',
                ],
              },
            ],
          },
        },
      },
    });

    render(<AgentExecutionTimeline />);

    fireEvent.click(screen.getByRole('button', { name: /展开执行过程/ }));
    expect(screen.getByText(/数据来源：firecrawl_searxng/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://finance.example.test/article-1' })).toBeInTheDocument();
    expect(screen.queryByText('来源 3 个')).not.toBeInTheDocument();
  });

  it('collapses a completed process but keeps the full process and reasoning log recoverable', () => {
    const reasoning = '先确认问题范围\n再核对公开资料';
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-history',
            stage: 'model',
            status: 'completed',
            summary: '已完成规划',
            occurred_at: '2026-09-02T10:00:00+08:00',
          },
          {
            event: 'agent_stage',
            run_id: 'run-history',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
            occurred_at: '2026-09-02T10:00:04+08:00',
          },
        ],
        custom: {},
      },
    });

    const { container } = render(<AgentExecutionTimeline reasoningText={reasoning} />);

    const toggle = screen.getByRole('button', { name: '展开执行过程，用时 4s' });
    const process = container.querySelector<HTMLElement>('[aria-label="执行过程"]')!;
    const details = container.querySelector<HTMLElement>('[role="region"][aria-label="执行过程详情"]')!;
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(process).toHaveClass('border-b');
    expect(process).not.toHaveClass('border-y');
    expect(details).toHaveAttribute('aria-hidden', 'true');
    expect(details).toHaveStyle({ gridTemplateRows: '0fr' });

    fireEvent.click(toggle);

    expect(screen.getByText('已完成规划')).toBeInTheDocument();
    expect(screen.getByText(/先确认问题范围/)).toBeInTheDocument();
    expect(details).toHaveAttribute('aria-hidden', 'false');
    expect(details).toHaveStyle({ gridTemplateRows: '1fr' });
    expect(screen.getByRole('button', { name: '收起执行过程，用时 4s' })).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByRole('button', { name: '收起执行过程，用时 4s' }));

    expect(details).toHaveAttribute('aria-hidden', 'true');
    expect(details).toHaveStyle({ gridTemplateRows: '0fr' });
    expect(screen.getByRole('button', { name: '展开执行过程，用时 4s' })).toBeInTheDocument();
  });

  it('keeps a waiting-for-approval process expanded because it is not terminal', () => {
    mockMessage({
      status: { type: 'requires-action', reason: 'interrupt' },
      metadata: {
        unstable_data: [{
          event: 'agent_stage',
          run_id: 'run-approval',
          stage: 'approval',
          status: 'started',
          summary: '等待用户确认',
        }],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline />);

    expect(screen.getByText('等待用户确认')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /展开执行过程/ })).not.toBeInTheDocument();
  });

  it('prefers assistant-ui message timing when it is available', () => {
    vi.mocked(useMessageTiming).mockReturnValue({
      streamStartTime: 1,
      totalStreamTime: 67 * 60 * 1_000 + 3_000,
      totalChunks: 1,
      toolCallCount: 0,
    });
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [{
          event: 'agent_stage',
          run_id: 'run-timing',
          stage: 'publish',
          status: 'completed',
          summary: '已发布最终回答',
        }],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline />);

    expect(screen.getByRole('button', { name: '展开执行过程，用时 1h 7m 3s' })).toBeInTheDocument();
  });
});
