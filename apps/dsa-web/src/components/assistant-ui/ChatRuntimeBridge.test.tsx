import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useThreadRuntime } from '@assistant-ui/react';
import type { ChatConversationDetail } from '../../api/agent';
import { ChatRuntimeBridge } from './ChatRuntimeBridge';

vi.mock('@assistant-ui/react', () => ({
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
});
