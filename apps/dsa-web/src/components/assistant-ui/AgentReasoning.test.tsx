import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMessage, useMessageTiming } from '@assistant-ui/react';
import { AgentExecutionTimeline, AgentToolCallPart } from './AgentReasoning';

vi.mock('@assistant-ui/react', () => ({
  useMessage: vi.fn(),
  useMessageTiming: vi.fn(),
}));

const mockMessage = (message: Record<string, unknown>) => {
  vi.mocked(useMessage).mockImplementation(((selector: (state: typeof message) => unknown) => (
    selector(message)
  )) as never);
};

const expandFirstPhase = () => {
  fireEvent.click(screen.getByRole('button', { name: /展开第 \d+ 阶段/ }));
};

const expandTool = (name: string) => {
  fireEvent.click(screen.getByRole('button', { name: `展开工具 ${name} 详情` }));
};

describe('AgentExecutionTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useMessageTiming).mockReturnValue(undefined);
  });

  it('renders a native tool part at its stream position', () => {
    mockMessage({
      metadata: {
        unstable_data: [{
          event: 'agent_stage',
          run_id: 'run-ordered',
          stage: 'tool',
          status: 'started',
          action_id: 'call-ordered',
          tool_call_id: 'call-ordered',
          summary: '正在读取主来源',
        }],
      },
    });

    render(
      <AgentToolCallPart
        type="tool-call"
        toolCallId="call-ordered"
        toolName="read_primary_source"
        args={{}}
        argsText="{}"
        status={{ type: 'running' }}
        addResult={vi.fn()}
        resume={vi.fn()}
        respondToApproval={vi.fn()}
      />,
    );

    expect(screen.getByText('正在读取主来源')).toBeInTheDocument();
    expect(screen.queryByText('执行原子工具 read_primary_source')).not.toBeInTheDocument();
    expect(screen.queryByText('进行中')).not.toBeInTheDocument();
  });

  it('normalizes the internal fallback summary into one concise tool line', () => {
    mockMessage({
      metadata: {
        unstable_data: [{
          event: 'agent_stage',
          run_id: 'run-compact',
          stage: 'tool',
          status: 'started',
          action_id: 'call-compact',
          tool_call_id: 'call-compact',
          summary: '执行原子工具 search_stocks',
        }],
      },
    });

    render(
      <AgentToolCallPart
        type="tool-call"
        toolCallId="call-compact"
        toolName="search_stocks"
        args={{}}
        argsText="{}"
        status={{ type: 'running' }}
        addResult={vi.fn()}
        resume={vi.fn()}
        respondToApproval={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '展开工具 search_stocks 详情' })).toBeInTheDocument();
    expect(screen.queryByText(/执行原子工具/)).not.toBeInTheDocument();
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

    const toggle = screen.getByRole('button', { name: /展开用时/ });
    const details = container.querySelector<HTMLElement>('[role="region"][aria-label="执行过程详情"]')!;
    expect(toggle).toBeInTheDocument();
    expect(details).toHaveAttribute('aria-hidden', 'true');
    expect(details).toHaveStyle({ gridTemplateRows: '0fr' });
    expect(details).toHaveClass('transition-[grid-template-rows]');
    fireEvent.click(toggle);
    expect(details).toHaveAttribute('aria-hidden', 'false');
    expect(details).toHaveStyle({ gridTemplateRows: '1fr' });
    expect(screen.getByRole('button', { name: '展开第 1 阶段' })).toHaveAttribute('aria-expanded', 'false');
    expandFirstPhase();
    expect(screen.getByText('search_web_source')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开工具 search_web_source 详情' })).toHaveAttribute('aria-expanded', 'false');
    expandTool('search_web_source');
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

    fireEvent.click(screen.getByRole('button', { name: /展开用时/ }));
    expandFirstPhase();
    expandTool('notify_user');
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

  it('shows evidence and semantic review beside native tool parts without duplicating tools', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-native-review',
            stage: 'model',
            status: 'completed',
            summary: '模型已完成研究回答',
            details: { model_turn: 1 },
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-review',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-native-review',
            summary: 'read_realtime_quote 已返回结果',
            details: { tool_name: 'read_realtime_quote' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-review',
            stage: 'evidence',
            status: 'completed',
            summary: '已核对 3 条结论与 2 条成功证据',
            details: { claim_count: 3, fact_claim_count: 2, inference_claim_count: 1 },
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-review',
            stage: 'reflection',
            status: 'completed',
            summary: '语义复核通过，研究回答允许发布',
            details: {
              verdict: 'pass',
              reflection_round: 1,
              reviewer_mode: 'self_refine',
              summary: '推理边界与风险表述均在现有证据范围内。',
            },
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-review',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline presentation="inline" stageOnly />);

    expect(screen.getByRole('region', { name: '校验与复核' })).toBeInTheDocument();
    expect(screen.getByText('关联证据')).toBeInTheDocument();
    expect(screen.getByText('语义复核')).toBeInTheDocument();
    expect(screen.getByText('复核结果：通过')).toBeInTheDocument();
    expect(screen.getByText('复核方式：受限自复核')).toBeInTheDocument();
    expect(screen.queryByText('read_realtime_quote')).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /展开用时/ }));
    expect(screen.getByRole('button', { name: '展开第 2 阶段' })).toBeInTheDocument();
    expect(screen.getAllByText('模型已给出候选回答，正在检查其证据关联')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: '展开第 2 阶段' }));
    expect(screen.getAllByText('模型已给出候选回答，正在检查其证据关联')).toHaveLength(2);
    expect(screen.queryByText(/候选回答正文不应/)).not.toBeInTheDocument();
    expect(screen.queryByText(/候选回答摘录/)).not.toBeInTheDocument();
  });

  it('keeps the phase collapsed until clicked and lets its tool expand independently', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-native-process',
            stage: 'model',
            status: 'completed',
            summary: '模型请求 1 个原子操作',
            details: { model_turn: 1 },
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-process',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-native',
            summary: 'search_stocks 已返回',
            details: { tool_name: 'search_stocks' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-process',
            stage: 'evidence',
            status: 'completed',
            summary: '已关联证据',
          },
          {
            event: 'agent_stage',
            run_id: 'run-native-process',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [{
              action_id: 'call-native',
              tool_name: 'search_stocks',
              success: true,
            }],
          },
        },
      },
    });

    render(<AgentExecutionTimeline />);

    fireEvent.click(screen.getByRole('button', { name: /展开用时/ }));
    expect(screen.getByRole('button', { name: '展开第 1 阶段' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: '展开工具 search_stocks 详情' })).not.toBeInTheDocument();
    expandFirstPhase();
    expect(screen.getByRole('button', { name: '展开工具 search_stocks 详情' })).toHaveAttribute('aria-expanded', 'false');
    expandTool('search_stocks');
    expect(screen.getByRole('button', { name: '收起工具 search_stocks 详情' })).toBeInTheDocument();
    expect(screen.getByText('已关联证据')).toBeInTheDocument();
    expect(screen.getByText('已发布最终回答')).toBeInTheDocument();
  });

  it('keeps parallel tools in one phase and expands each tool independently', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-parallel-phase',
            round_id: 'round-1',
            stage: 'model',
            status: 'completed',
            summary: '第 1 轮：模型请求 2 个原子操作',
            details: {
              model_turn: 1,
              operations: [
                { tool_name: 'search_stocks', arguments: { query: '新强联' } },
                { tool_name: 'read_realtime_quote', arguments: { symbol: '300850' } },
              ],
            },
          },
          {
            event: 'agent_stage',
            run_id: 'run-parallel-phase',
            round_id: 'round-1',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-search',
            summary: 'search_stocks 已返回结果',
            details: { tool_name: 'search_stocks' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-parallel-phase',
            round_id: 'round-1',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-quote',
            summary: 'read_realtime_quote 已返回结果',
            details: { tool_name: 'read_realtime_quote' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-parallel-phase',
            round_id: 'round-1',
            stage: 'publish',
            status: 'completed',
            summary: '已发布最终回答',
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [
              {
                action_id: 'call-search',
                tool_name: 'search_stocks',
                arguments: { query: '新强联' },
                success: true,
                result_count: 1,
              },
              {
                action_id: 'call-quote',
                tool_name: 'read_realtime_quote',
                arguments: { symbol: '300850' },
                success: true,
                result_count: 1,
              },
            ],
          },
        },
      },
    });

    render(<AgentExecutionTimeline />);

    fireEvent.click(screen.getByRole('button', { name: /展开用时/ }));
    const phase = screen.getByRole('button', { name: '展开第 1 阶段' });
    expect(phase).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: '展开工具 search_stocks 详情' })).not.toBeInTheDocument();

    fireEvent.click(phase);
    const search = screen.getByRole('button', { name: '展开工具 search_stocks 详情' });
    const quote = screen.getByRole('button', { name: '展开工具 read_realtime_quote 详情' });
    expect(search).toHaveAttribute('aria-expanded', 'false');
    expect(quote).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(search);
    expect(screen.getByRole('button', { name: '收起工具 search_stocks 详情' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '展开工具 read_realtime_quote 详情' })).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(quote);
    expect(screen.getByRole('button', { name: '收起工具 search_stocks 详情' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '收起工具 read_realtime_quote 详情' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('does not expose a model-only lifecycle marker while the run is active', () => {
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

    expect(screen.queryByRole('button', { name: '展开第 1 阶段' })).not.toBeInTheDocument();
    expect(screen.queryByText(summary)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /展开用时/ })).not.toBeInTheDocument();
  });

  it('keeps a completed phase completed while a later phase is still running', () => {
    mockMessage({
      status: { type: 'running' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-phase-status',
            stage: 'model',
            status: 'completed',
            summary: '第 1 轮已完成工具选择',
            details: { model_turn: 1 },
          },
          {
            event: 'agent_stage',
            run_id: 'run-phase-status',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-phase-one',
            summary: 'search_stocks 已返回',
            details: { tool_name: 'search_stocks' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-phase-status',
            stage: 'model',
            status: 'started',
            summary: '第 2 轮正在决定下一步',
            details: { model_turn: 2 },
          },
          {
            event: 'agent_stage',
            run_id: 'run-phase-status',
            stage: 'tool',
            status: 'started',
            action_id: 'call-phase-two',
            summary: '正在读取补充数据',
            details: { tool_name: 'read_market_data' },
          },
        ],
        custom: {},
      },
    });

    render(<AgentExecutionTimeline />);

    expect(screen.getByText('已完成 1 个工具')).toBeInTheDocument();
    expect(screen.getByText('正在执行 1 个工具')).toBeInTheDocument();
  });

  it('shows completed and failed tool counts separately', () => {
    mockMessage({
      status: { type: 'running' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-mixed-tools',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-good',
            summary: 'read_quote 已返回',
            details: { tool_name: 'read_quote' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-mixed-tools',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-bad',
            summary: 'read_news 调用失败',
            details: { tool_name: 'read_news' },
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [
              {
                action_id: 'call-good',
                tool_name: 'read_quote',
                success: true,
              },
              {
                action_id: 'call-bad',
                tool_name: 'read_news',
                success: false,
                errors: ['DataNotReady'],
              },
            ],
          },
        },
      },
    });

    render(<AgentExecutionTimeline />);

    expect(screen.getByText('已完成 1 个工具，失败 1 个工具')).toBeInTheDocument();
  });

  it('does not mark a published answer as incomplete for tool-level failures', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-published-with-gap',
            stage: 'tool',
            status: 'failed',
            action_id: 'call-failed',
            summary: 'read_news 执行失败',
            error_code: 'provider_unavailable',
            details: { tool_name: 'read_news' },
          },
          {
            event: 'agent_stage',
            run_id: 'run-published-with-gap',
            stage: 'publish',
            status: 'completed',
            summary: '已发布带限制的回答',
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [{
              action_id: 'call-failed',
              tool_name: 'read_news',
              success: false,
              errors: ['上游暂不可用'],
            }],
          },
        },
      },
    });

    render(<AgentExecutionTimeline />);

    expect(screen.getByRole('button', { name: /展开用时/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /展开执行未完成/ })).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /展开用时/ }));
    expandFirstPhase();
    expandTool('search_web_source');
    expect(screen.getByText(/数据来源：firecrawl_searxng/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://finance.example.test/article-1' })).toBeInTheDocument();
    expect(screen.queryByText('来源 3 个')).not.toBeInTheDocument();
  });

  it('collapses a completed process but keeps planning text and reasoning recoverable', () => {
    const processText = '先确认问题范围\n再核对公开资料';
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

    const { container } = render(
      <AgentExecutionTimeline processText={processText} reasoningText={reasoning} />,
    );

    const toggle = screen.getByRole('button', { name: '展开用时 4s' });
    const process = container.querySelector<HTMLElement>('[aria-label="执行过程"]')!;
    const details = container.querySelector<HTMLElement>('[role="region"][aria-label="执行过程详情"]')!;
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveClass('border-b');
    expect(process).not.toHaveClass('border-y');
    expect(details).toHaveAttribute('aria-hidden', 'true');
    expect(details).toHaveStyle({ gridTemplateRows: '0fr' });
    expect(screen.queryByText(processText)).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(screen.getAllByText('已完成规划')).toHaveLength(2);
    expect(screen.getAllByText(/先确认问题范围/)).toHaveLength(1);
    expect(details).toHaveAttribute('aria-hidden', 'false');
    expect(details).toHaveStyle({ gridTemplateRows: '1fr' });
    expect(screen.getByRole('button', { name: '收起用时 4s' })).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByRole('button', { name: '收起用时 4s' }));

    expect(details).toHaveAttribute('aria-hidden', 'true');
    expect(details).toHaveStyle({ gridTemplateRows: '0fr' });
    expect(screen.getByRole('button', { name: '展开用时 4s' })).toBeInTheDocument();
  });

  it('can render a legacy trace inline with independently collapsible tools', () => {
    mockMessage({
      status: { type: 'complete' },
      metadata: {
        unstable_data: [
          {
            event: 'agent_stage',
            run_id: 'run-inline',
            stage: 'model',
            status: 'completed',
            summary: '先确认来源，再整理结论',
            details: { model_turn: 1 },
          },
          {
            event: 'agent_stage',
            run_id: 'run-inline',
            stage: 'tool',
            status: 'completed',
            action_id: 'call-inline',
            summary: 'read_source 已返回结果',
            details: { tool_name: 'read_source' },
          },
        ],
        custom: {
          agent_execution_trace: {
            tool_results: [{
              action_id: 'call-inline',
              tool_name: 'read_source',
              success: true,
            }],
          },
        },
      },
    });

    render(<AgentExecutionTimeline presentation="inline" />);

    expect(screen.getByText('先确认来源，再整理结论')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开工具 read_source 详情' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /展开用时/ })).not.toBeInTheDocument();
  });

  it('keeps a waiting-for-approval process collapsible while it is not terminal', () => {
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

    const phase = screen.getByRole('button', { name: '展开第 1 阶段' });
    const details = document.querySelector<HTMLElement>('[role="region"][aria-label="第 1 阶段详情"]')!;
    expect(phase).toHaveAttribute('aria-expanded', 'false');
    expect(details).toHaveAttribute('aria-hidden', 'true');

    fireEvent.click(phase);

    expect(phase).toHaveAttribute('aria-expanded', 'true');
    expect(details).toHaveAttribute('aria-hidden', 'false');
    expect(screen.queryByRole('button', { name: /展开用时/ })).not.toBeInTheDocument();
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

    expect(screen.getByRole('button', { name: '展开用时 1h 7m 3s' })).toBeInTheDocument();
  });
});
