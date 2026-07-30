import { beforeEach, describe, expect, it, vi } from 'vitest';

import { agentApi } from '../agent';

const put = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put,
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('agentApi.syncConversationSnapshot', () => {
  beforeEach(() => {
    put.mockReset();
    put.mockResolvedValue({
      data: {
        id: 'c1',
        title: '对话',
        title_source: 'auto',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
        messages: [],
      },
    });
  });

  it('does not send an empty message list for a thread-only snapshot', async () => {
    await agentApi.syncConversationSnapshot('c1', {
      threadState: { messages: [] },
    });

    expect(put).toHaveBeenCalledWith(
      '/api/v1/agent/conversations/c1/snapshot',
      { thread_state: { messages: [] } },
    );
  });

  it('requests server-context pruning only for an edited transcript', async () => {
    await agentApi.syncConversationSnapshot('c1', {
      messages: [{ id: 'u1', role: 'user', content: '保留问题' }],
      threadState: { messages: [] },
      pruneAgentContextToMessages: true,
    });

    expect(put).toHaveBeenCalledWith(
      '/api/v1/agent/conversations/c1/snapshot',
      {
        messages: [{ id: 'u1', role: 'user', content: '保留问题' }],
        thread_state: { messages: [] },
        prune_agent_context_to_messages: true,
      },
    );
  });
});
