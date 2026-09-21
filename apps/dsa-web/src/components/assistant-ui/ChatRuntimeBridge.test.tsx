import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import type { ChatConversationDetail } from '../../api/agent';
import { ChatRuntimeBridge } from './ChatRuntimeBridge';
import { toRuntimeMessages } from './ChatRuntimeBridgeUtils';

vi.mock('@assistant-ui/react', () => ({
  useThread: vi.fn(),
  useThreadRuntime: vi.fn(),
}));

const makeDetail = (withToolPart = false): ChatConversationDetail => ({
  id: 'conversation-1',
  title: '历史对话',
  titleSource: 'manual',
  previewText: '完整回答',
  createdAt: '2026-07-17T10:00:00Z',
  updatedAt: '2026-07-17T10:01:00Z',
  messages: [
    {
      id: 'user-1',
      conversationId: 'conversation-1',
      role: 'user',
      content: '问题',
      sequence: 0,
      createdAt: '2026-07-17T10:00:00Z',
    },
    {
      id: 'assistant-1',
      conversationId: 'conversation-1',
      role: 'assistant',
      content: '完整回答',
      sequence: 1,
      createdAt: '2026-07-17T10:01:00Z',
    },
  ],
  threadState: {
    headId: 'assistant-1',
    messages: [
      {
        parentId: null,
        message: {
          id: 'assistant-1',
          role: 'assistant',
          // The persisted snapshot intentionally contains only the current
          // minimal message shape; typed display parts are sourced from the
          // canonical execution trace below.
          content: withToolPart
            ? [{ type: 'tool-call', toolName: 'broken-tool' }]
            : [{ type: 'text', text: '完整回答' }],
        },
      },
    ],
  },
  isGenerating: false,
  resumeState: {
    active: false,
    afterChunkIndex: 0,
    assistantText: '',
  },
});

describe('ChatRuntimeBridge', () => {
  const runtime = {
    cancelRun: vi.fn(),
    reset: vi.fn(),
    import: vi.fn(),
    startRun: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useThread).mockReturnValue(false);
    vi.mocked(useThreadRuntime).mockReturnValue(
      runtime as unknown as ReturnType<typeof useThreadRuntime>,
    );
  });

  it('rehydrates the durable native parts in their original text/tool order', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        {
          type: 'text',
          text: '先确认取证范围。',
          displayKind: 'progress',
          roundId: '1',
        },
        {
          type: 'tool-call',
          toolCallId: 'call-1',
          toolName: 'read_source',
          argsText: '{"url":"https://example.com"}',
          result: { success: true },
          roundId: '1',
        },
        {
          type: 'text',
          text: '最终回答',
          displayKind: 'answer',
          roundId: '1',
        },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-1',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(content.map((part) => part.type)).toEqual(['text', 'tool-call', 'text']);
    expect(content[0]?.text).toBe('先确认取证范围。');
    expect(content[1]?.toolCallId).toBe('call-1');
    expect(content[1]?.toolName).toBe('read_source');
    expect(content[1]?.argsText).toBe('{"url":"https://example.com"}');
    expect((content[1]?.providerMetadata as Record<string, unknown>).dsa).toEqual({
      displayKind: 'progress',
      roundId: '1',
    });
    expect(content[2]?.text).toBe('最终回答');
  });

  it('rehydrates typed stages and charts at their original stream positions', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        { type: 'text', text: '先确认行情数据。', displayKind: 'progress' },
        {
          type: 'data',
          name: 'agent-stage',
          data: {
            event: 'agent_stage',
            run_id: 'run-typed-parts',
            stage: 'planning',
            status: 'completed',
            summary: '行情方向已完成交接',
          },
        },
        {
          type: 'tool-call',
          toolCallId: 'call-typed',
          toolName: 'read_source',
          argsText: '{}',
          result: { success: true },
        },
        { type: 'text', text: '下面展示对应的资金变化。', displayKind: 'progress' },
        {
          type: 'data',
          name: 'stock-chart',
          data: {
            chart_id: 'chart-typed',
            chart_type: 'line',
            title: '资金变化',
            series: [{ key: 'close', label: '收盘价' }],
            data: [{ x: '2026-09-16', close: 12.3 }],
          },
        },
        { type: 'text', text: '最终回答', displayKind: 'answer' },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-typed-parts',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(content.map((part) => part.type)).toEqual([
      'text',
      'data',
      'tool-call',
      'text',
      'data',
      'text',
    ]);
    expect(content[1]?.name).toBe('agent-stage');
    expect(content[2]?.toolCallId).toBe('call-typed');
    expect(content[4]?.name).toBe('stock-chart');
    expect((content[4]?.data as Record<string, unknown>).chart_id).toBe('chart-typed');
    expect(content[5]?.text).toBe('最终回答');
  });

  it('uses the complete committed Team answer rather than a truncated trace prefix', () => {
    const detail = makeDetail(false);
    const answer = '# 买入评估\n\n## 结论\n有条件的判断。\n\n## 行情\n行情证据。\n\n## 基本面\n财务证据。\n\n## 新闻\n新闻证据。';
    detail.messages[1]!.content = answer;
    detail.executionTrace = {
      team: { status: 'partial' },
      displayParts: [
        { type: 'data', name: 'team-model-projection', partId: 'plan', data: { text: '模型的规划' } },
        { type: 'text', text: '# 买入评估\n\n## 行情\n只有这一段', displayKind: 'answer' },
      ],
    };
    const messages = toRuntimeMessages(detail.id, detail.messages, undefined, detail.executionTrace, 'team-run', answer);
    const content = messages.find((message) => message.id === 'assistant-1')!.content as Array<Record<string, unknown>>;
    expect(content.filter((part) => part.type === 'text').map((part) => part.text)).toEqual([answer]);
    expect(content[0]?.name).toBe('team-model-projection');
  });

  it('repairs legacy joined answer headings and hides one-point quote charts', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        {
          type: 'text',
          text: '已完成行情核验。 ## 二、基本面\n\n基本面结果。',
          displayKind: 'answer',
        },
        {
          type: 'data',
          name: 'stock-chart',
          data: {
            chart_id: 'quote-only',
            chart_type: 'line',
            title: '贵州茅台近期行情走势',
            action_id: 'quote-action',
            series: [{ key: 'price', label: 'price' }],
            data: [{ x: '600519', price: 1257.05 }],
          },
        },
        {
          type: 'data',
          name: 'stock-chart',
          data: {
            chart_id: 'kline',
            chart_type: 'line',
            title: '贵州茅台近期行情走势',
            action_id: 'kline-action',
            series: [{ key: 'close', label: 'close' }],
            data: [
              { x: '2026-09-15', close: 1272.75 },
              { x: '2026-09-16', close: 1257.05 },
            ],
          },
        },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-legacy-answer-boundary',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(content[0]?.text).toContain('已完成行情核验。\n\n## 二、基本面');
    expect(content.filter((part) => part.name === 'stock-chart')).toHaveLength(1);
    expect((content.find((part) => part.name === 'stock-chart')?.data as Record<string, unknown>).chart_id)
      .toBe('kline');
  });

  it('merges a repeated section number during historical replay without a duplicate heading', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        {
          type: 'text',
          text: '## 三、新闻与研报动态\n\n研报内容。',
          displayKind: 'answer',
        },
        {
          type: 'text',
          text: '## 三、新闻与公告动态\n\n公告内容。\n\n## 四、主要风险\n\n风险内容。',
          displayKind: 'answer',
        },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-repeated-section-number',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;
    const answerText = content
      .filter((part) => part.type === 'text')
      .map((part) => String(part.text || ''))
      .join('\n');

    expect(answerText).toContain('## 三、新闻与研报动态');
    expect(answerText).not.toContain('## 三、新闻与公告动态');
    expect(answerText).toContain('公告内容。');
    expect(answerText).toContain('## 四、主要风险');
  });

  it('preserves legacy Planning progress while rehydrating terminal history', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        {
          type: 'text',
          text: '我先根据你的目标制定了 2 步研究计划。\n\n接下来，我会核验证券身份。',
          displayKind: 'progress',
        },
        {
          type: 'text',
          text: '已完成当前步骤：核验证券身份：已获得 1 条工具观察。',
          displayKind: 'progress',
        },
        {
          type: 'text',
          text: '最终回答',
          displayKind: 'answer',
        },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-legacy-planning',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(content[0]?.text).toBe('我先根据你的目标制定了 2 步研究计划。\n\n');
    expect(content[1]?.text).toBe('接下来，我会核验证券身份。');
    expect(content[2]?.text).toBe('已完成当前步骤：核验证券身份：已获得 1 条工具观察。');
  });

  it('coalesces duplicate persisted progress without changing the transcript order', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        { type: 'text', text: '正在核对来源。', displayKind: 'progress' },
        { type: 'text', text: '正在核对来源。', displayKind: 'progress' },
        {
          type: 'tool-call',
          toolCallId: 'call-1',
          toolName: 'read_source',
          argsText: '{}',
          result: { success: true },
          displayKind: 'progress',
        },
        { type: 'text', text: '正在核对来源。', displayKind: 'progress' },
        { type: 'text', text: '最终回答', displayKind: 'answer' },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-duplicate-progress',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(content.map((part) => part.type)).toEqual(['text', 'tool-call', 'text']);
    expect(content.map((part) => part.text)).toEqual([
      '正在核对来源。',
      undefined,
      '最终回答',
    ]);
    expect(content[2]?.text).toBe('最终回答');
  });

  it('coalesces wording-only progress revisions during terminal replay', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '最终回答';
    detail.executionTrace = {
      displayParts: [
        {
          type: 'text',
          text: '市场宽度已确认：上涨家数多于下跌家数（2946 vs 2086），整体偏强，但主要指数行情因数据源未就绪而获取失败，需要重试。',
          displayKind: 'progress',
        },
        {
          type: 'text',
          text: '市场宽度已确认：上涨家数明显多于下跌家数（2946 vs 2086），整体环境偏强，但主要指数行情因数据源未就绪而两次获取失败，需要重试。',
          displayKind: 'progress',
        },
        { type: 'text', text: '最终回答', displayKind: 'answer' },
      ],
    };

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      detail.executionTrace,
      'run-near-duplicate-progress',
      '最终回答',
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(content.map((part) => part.text)).toEqual([
      '市场宽度已确认：上涨家数多于下跌家数（2946 vs 2086），整体偏强，但主要指数行情因数据源未就绪而获取失败，需要重试。',
      '最终回答',
    ]);
  });

  it('restores the execution disclosure for every assistant turn in history', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '第一轮回答';
    detail.messages.push(
      {
        id: 'user-2',
        conversationId: detail.id,
        role: 'user',
        content: '第二个问题',
        sequence: 2,
        createdAt: '2026-07-17T10:02:00Z',
      },
      {
        id: 'assistant-2',
        conversationId: detail.id,
        role: 'assistant',
        content: '第二轮回答',
        sequence: 3,
        createdAt: '2026-07-17T10:03:00Z',
      },
    );
    const traceHistory = [
      {
        runId: 'run-1',
        finalText: '第一轮回答【证据 ev-1】',
        createdAt: '2026-09-20T22:58:03Z',
        updatedAt: '2026-09-20T23:08:28Z',
        executionTrace: {
          displayParts: [
            { type: 'text', text: '第一轮已完成取证。', displayKind: 'progress' },
            { type: 'text', text: '第一轮回答', displayKind: 'answer' },
          ],
        },
      },
      {
        runId: 'run-2',
        finalText: '第二轮回答【证据 ev-2】',
        executionTrace: {
          displayParts: [
            { type: 'text', text: '第二轮已完成取证。', displayKind: 'progress' },
            { type: 'text', text: '第二轮回答', displayKind: 'answer' },
          ],
        },
      },
    ];
    detail.executionTraces = [
      ...traceHistory,
    ];

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      traceHistory[1]!.executionTrace,
      'run-2',
      '第二轮回答',
      false,
      traceHistory,
    );
    const first = messages.find((message) => message.id === 'assistant-1');
    const second = messages.find((message) => message.id === 'assistant-2');
    expect(first?.metadata?.custom?.agent_run_duration_ms).toBe(625_000);

    expect((first?.content as unknown as Array<Record<string, unknown>>).map((part) => part.text))
      .toEqual(['第一轮已完成取证。', '第一轮回答']);
    expect((second?.content as unknown as Array<Record<string, unknown>>).map((part) => part.text))
      .toEqual(['第二轮已完成取证。', '第二轮回答']);
    expect(
      (first?.metadata as Record<string, unknown>)?.custom,
    ).toEqual({ agent_execution_trace: traceHistory[0]!.executionTrace, agent_run_duration_ms: 625_000 });
    expect(
      (second?.metadata as Record<string, unknown>)?.custom,
    ).toEqual({ agent_execution_trace: traceHistory[1]!.executionTrace });
  });

  it('hides legacy internal Planning diagnostics in chat replay', () => {
    const detail = makeDetail(false);
    detail.messages[1]!.content = '已保留部分结果\n\n[本轮结果存在未完成的核验：PlanningStepReport failed after 2 attempts (planning_contract_validation_failed)]';

    const messages = toRuntimeMessages(
      detail.id,
      detail.messages,
      undefined,
      undefined,
      'run-legacy-diagnostic',
      detail.messages[1]!.content,
    );
    const assistant = messages.find((message) => message.id === 'assistant-1');
    const content = assistant?.content as unknown as Array<Record<string, unknown>>;

    expect(String(content[0]?.text)).not.toContain('PlanningStepReport');
    expect(String(content[0]?.text)).toContain('计划尚未完整结束');
  });

  it('restores plain text history from canonical messages instead of importing thread state', async () => {
    render(
      <ChatRuntimeBridge
        conversationDetail={makeDetail(false)}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.import).not.toHaveBeenCalled();
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({ id: 'assistant-1', role: 'assistant' }),
      ]));
    });
  });

  it('does not hydrate a terminal status as an assistant answer', async () => {
    const detail = makeDetail(false);
    detail.messages.splice(1, 0, {
      id: 'assistant-timeout',
      conversationId: detail.id,
      role: 'assistant',
      content: '上游模型服务返回超时；已保留已有工具观察和证据。',
      sequence: 1,
      createdAt: '2026-07-17T10:00:30Z',
    });

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      const hydrated = vi.mocked(runtime.reset).mock.calls.at(-1)?.[0] as Array<{ id?: string }>;
      expect(hydrated?.some((message) => message.id === 'assistant-timeout')).toBe(false);
      expect(hydrated?.some((message) => message.id === 'assistant-1')).toBe(true);
    });
  });

  it('ignores opaque tool thread state and restores canonical messages', async () => {
    render(
      <ChatRuntimeBridge
        conversationDetail={makeDetail(true)}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.import).not.toHaveBeenCalled();
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({ id: 'assistant-1', role: 'assistant' }),
      ]));
    });
  });

  it('does not import historical reasoning parts as live UI state', async () => {
    const detail = makeDetail(false);
    detail.threadState!.messages[0]!.message.content = [
      { type: 'reasoning', text: '先核验事实。' },
      { type: 'text', text: '完整回答' },
    ];

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.import).not.toHaveBeenCalled();
      expect(runtime.reset).toHaveBeenCalled();
    });
  });

  it('hydrates the persisted terminal stage into plain assistant history', async () => {
    const detail = makeDetail(false);
    detail.resumeState = {
      active: false,
      status: 'failed',
      afterChunkIndex: 0,
      assistantText: '',
      latestStage: {
        event: 'agent_stage_v2',
        runId: 'run-failed',
        stage: 'completed',
        status: 'failed',
        errorCode: 'planner_schema_invalid',
        summary: '板块绑定失败',
      },
    };

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({
          id: 'conversation-1-agent-trace-run-failed',
          role: 'assistant',
          content: [],
          metadata: {
            unstable_data: [detail.resumeState!.latestStage],
          },
        }),
      ]));
    });
  });

  it('uses the persisted terminal stage instead of stale rich-history state', async () => {
    const detail = makeDetail(false);
    detail.threadState!.messages[0]!.message.content = [
      { type: 'reasoning', text: '模型仍在处理，已等待 816 秒' },
      { type: 'text', text: '任务失败' },
    ];
    detail.threadState!.messages[0]!.message.metadata = {
      unstable_data: [{
        event: 'agent_stage_v2',
        run_id: 'run-failed',
        stage: 'catalog_mapping',
        status: 'started',
        summary: '仍在处理',
      }],
    };
    detail.resumeState = {
      active: false,
      status: 'failed',
      afterChunkIndex: 0,
      assistantText: '',
      latestStage: {
        event: 'agent_stage_v2',
        runId: 'run-failed',
        stage: 'completed',
        status: 'failed',
        summary: '本轮已经失败',
      },
    };

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.import).not.toHaveBeenCalled();
      const resetMessages = runtime.reset.mock.calls.at(-1)?.[0] as Array<{
        metadata?: { unstable_data?: unknown[] };
      }>;
      const events = resetMessages.find((message) => message.metadata?.unstable_data)?.metadata?.unstable_data;
      expect(events?.at(-1)).toBe(detail.resumeState!.latestStage);
    });
  });

  it('does not restart a cancelled run whose persisted history ends with a user message', async () => {
    const detail = makeDetail(false);
    detail.messages = detail.messages.slice(0, 1);
    detail.threadState = null;
    detail.resumeState = {
      active: false,
      status: 'cancelled',
      afterChunkIndex: 0,
      assistantText: '',
    };

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.reset).toHaveBeenCalled();
      expect(runtime.startRun).not.toHaveBeenCalled();
    });
  });

  it('does not replay a terminal failed run when a stale payload still marks it active', async () => {
    const detail = makeDetail(false);
    detail.messages = detail.messages.slice(0, 1);
    detail.threadState = null;
    detail.executionTrace = {
      displayParts: [{
        type: 'tool-call',
        toolCallId: 'action-1',
        toolName: 'search_news',
        result: { success: true },
      }],
    };
    detail.resumeState = {
      runId: 'run-failed',
      active: true,
      status: 'failed',
      afterChunkIndex: 0,
      assistantText: '',
      hasToolEvents: true,
      latestStage: {
        event: 'agent_stage_v2',
        runId: 'run-failed',
        stage: 'completed',
        status: 'failed',
        summary: '模型服务不可用，已结束本轮执行',
      },
    };

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.startRun).not.toHaveBeenCalled();
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({
          id: 'conversation-1-agent-trace-run-failed',
          role: 'assistant',
          content: [expect.objectContaining({
            type: 'tool-call',
            toolCallId: 'action-1',
            toolName: 'search_news',
          })],
          metadata: expect.objectContaining({
            unstable_data: [detail.resumeState!.latestStage],
          }),
        }),
      ]));
    });
  });

  it('does not hydrate a stale detail snapshot over a locally running turn', async () => {
    vi.mocked(useThread).mockReturnValue(true);

    render(
      <ChatRuntimeBridge
        conversationDetail={makeDetail(false)}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.cancelRun).not.toHaveBeenCalled();
      expect(runtime.reset).not.toHaveBeenCalled();
      expect(runtime.import).not.toHaveBeenCalled();
    });
  });

  it('detaches a stale local stream when the durable run has already ended', async () => {
    vi.mocked(useThread).mockReturnValue(true);
    const detail = makeDetail(false);
    detail.resumeState = {
      runId: 'run-terminal',
      active: false,
      status: 'partial',
      afterChunkIndex: 0,
      assistantText: '已安全结束的部分结果',
      latestStage: {
        event: 'agent_stage',
        runId: 'run-terminal',
        stage: 'publish',
        status: 'completed',
        summary: '答案与运行终态已准备原子发布',
      },
    };

    const view = render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.cancelRun).toHaveBeenCalledOnce();
      expect(runtime.reset).not.toHaveBeenCalled();
    });

    vi.mocked(useThread).mockReturnValue(false);
    view.rerender(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({ id: 'assistant-1', role: 'assistant' }),
      ]));
    });
  });

  it('does not detach a fresh local turn because the loaded detail belongs to a prior terminal run', async () => {
    vi.mocked(useThread).mockReturnValue(true);
    const detail = makeDetail(false);
    detail.resumeState = {
      runId: 'run-prior-terminal',
      active: false,
      status: 'cancelled',
      afterChunkIndex: 0,
      assistantText: '',
    };
    const shouldDetachTerminalStream = vi.fn().mockReturnValue(false);

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
        shouldDetachTerminalStream={shouldDetachTerminalStream}
      />,
    );

    await waitFor(() => {
      expect(shouldDetachTerminalStream).toHaveBeenCalledWith('conversation-1', 'run-prior-terminal');
      expect(runtime.cancelRun).not.toHaveBeenCalled();
      expect(runtime.reset).not.toHaveBeenCalled();
    });
  });

  it('waits for a running local stream to detach before replacing its message tree', async () => {
    vi.mocked(useThread).mockReturnValue(true);
    const firstDetail = makeDetail(false);
    const view = render(
      <ChatRuntimeBridge
        conversationDetail={firstDetail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.cancelRun).not.toHaveBeenCalled();
      expect(runtime.reset).not.toHaveBeenCalled();
    });

    const nextDetail = makeDetail(false);
    nextDetail.id = 'conversation-2';
    nextDetail.messages = nextDetail.messages.map((message) => ({
      ...message,
      conversationId: nextDetail.id,
    }));

    view.rerender(
      <ChatRuntimeBridge
        conversationDetail={nextDetail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.cancelRun).toHaveBeenCalledOnce();
      expect(runtime.reset).not.toHaveBeenCalled();
    });

    vi.mocked(useThread).mockReturnValue(false);
    view.rerender(
      <ChatRuntimeBridge
        conversationDetail={nextDetail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({ id: 'assistant-1' }),
      ]));
    });
  });

  it('resumes a long active run from the server cursor instead of replaying from zero', async () => {
    const detail = makeDetail(false);
    detail.messages.push({
      id: 'user-2',
      conversationId: detail.id,
      role: 'user',
      content: '重试',
      sequence: 2,
      createdAt: '2026-07-17T10:02:00Z',
    });
    detail.isGenerating = true;
    detail.resumeState = {
      active: true,
      status: 'running',
      afterChunkIndex: 21_902,
      assistantText: '',
      hasToolEvents: true,
    };
    const prepareResume = vi.fn();

    render(
      <ChatRuntimeBridge
        conversationDetail={detail}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={prepareResume}
      />,
    );

    await waitFor(() => {
      expect(prepareResume).toHaveBeenLastCalledWith(detail.id, 21_902);
      expect(runtime.startRun).toHaveBeenCalledOnce();
    });
  });
});
