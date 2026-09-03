import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import type { ChatConversationDetail } from '../../api/agent';
import { ChatRuntimeBridge, toRuntimeMessages } from './ChatRuntimeBridge';

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
          // 模拟旧版/手工写入的精简快照；它没有完整 assistant-ui 元数据。
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
      executionTrace: {
        toolResults: [{ action_id: 'action-1', tool_name: 'search_news', success: true }],
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
          content: [],
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
