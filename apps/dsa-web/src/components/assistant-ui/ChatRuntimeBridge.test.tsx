import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import type { ChatConversationDetail } from '../../api/agent';
import { ChatRuntimeBridge } from './ChatRuntimeBridge';

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

  it('falls back to canonical messages when a tool thread state cannot be imported', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    runtime.import.mockImplementationOnce(() => {
      throw new Error('invalid exported repository');
    });

    render(
      <ChatRuntimeBridge
        conversationDetail={makeDetail(true)}
        onThreadRuntime={vi.fn()}
        onPrepareResumeExisting={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(runtime.import).toHaveBeenCalledOnce();
      expect(runtime.reset).toHaveBeenLastCalledWith(expect.arrayContaining([
        expect.objectContaining({ id: 'assistant-1', role: 'assistant' }),
      ]));
    });
    warn.mockRestore();
  });

  it('imports thread state when it contains model reasoning', async () => {
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
      expect(runtime.import).toHaveBeenCalledOnce();
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
          id: 'assistant-1',
          metadata: {
            unstable_data: [detail.resumeState!.latestStage],
          },
        }),
      ]));
    });
  });

  it('overrides stale rich-history status with the persisted terminal stage', async () => {
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
      const imported = runtime.import.mock.calls.at(-1)?.[0] as {
        messages: Array<{ message: { metadata: { unstable_data: unknown[] } } }>;
      };
      const events = imported.messages[0]!.message.metadata.unstable_data;
      expect(events.at(-1)).toBe(detail.resumeState!.latestStage);
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
