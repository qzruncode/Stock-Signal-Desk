import { beforeEach, describe, expect, it, vi } from 'vitest';

import { agentApi } from '../agent';

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: {
    get,
    post: vi.fn(),
    put,
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('agentApi.syncConversationSnapshot', () => {
  beforeEach(() => {
    get.mockReset();
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

  it('preserves snake_case tool payloads inside the opaque persisted thread state', async () => {
    get.mockResolvedValue({
      data: {
        id: 'c1',
        title: '对话',
        title_source: 'auto',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
        messages: [],
        thread_state: {
          headId: 'a1',
          messages: [{
            message: {
              id: 'a1',
              role: 'assistant',
              content: [{
                type: 'tool-call',
                toolName: 'discover_rss_sources',
                result: {
                  catalog_count: 47,
                  matched_count: 15,
                  returned_count: 15,
                  items: [{
                    route_path: '/szse/disclosure/listed/notice/:query?',
                    namespace_name: '深圳证券交易所',
                  }],
                },
              }],
            },
            parentId: null,
          }],
        },
      },
    });

    const detail = await agentApi.getConversation('c1');
    const result = (detail.threadState?.messages[0]?.message.content as Array<Record<string, unknown>>)[0]?.result as Record<string, unknown>;

    expect(detail.createdAt).toBe('2026-07-26T00:00:00Z');
    expect(result).toMatchObject({ catalog_count: 47, matched_count: 15, returned_count: 15 });
    expect((result.items as Array<Record<string, unknown>>)[0]).toMatchObject({
      route_path: '/szse/disclosure/listed/notice/:query?',
      namespace_name: '深圳证券交易所',
    });
  });
});
