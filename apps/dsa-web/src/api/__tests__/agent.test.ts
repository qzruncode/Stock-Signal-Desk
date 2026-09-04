import { beforeEach, describe, expect, it, vi } from 'vitest';

import { agentApi } from '../agent';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());
const deleteRequest = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: {
    get,
    post,
    put,
    patch: vi.fn(),
    delete: deleteRequest,
  },
}));

describe('agentApi.syncConversationSnapshot', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    put.mockReset();
    deleteRequest.mockReset();
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

  it('clears all conversations through the collection endpoint', async () => {
    deleteRequest.mockResolvedValue({ data: { deleted: 3 } });

    await expect(agentApi.clearAllConversations()).resolves.toBe(3);

    expect(deleteRequest).toHaveBeenCalledWith('/api/v1/agent/conversations');
  });

  it('sends a server-verifiable approval decision to the pending interrupt', async () => {
    post.mockResolvedValue({ data: { accepted: true } });

    await expect(agentApi.decideInterrupt('c1', 'interrupt-1', {
      runId: 'run-1',
      fingerprint: 'fingerprint-1',
      decision: 'approve',
    })).resolves.toBeUndefined();
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agent/conversations/c1/interrupts/interrupt-1/decision',
      {
        run_id: 'run-1',
        fingerprint: 'fingerprint-1',
        decision: 'approve',
      },
    );
  });

  it('does not send an empty message list for a metadata-only snapshot', async () => {
    await agentApi.syncConversationSnapshot('c1', {});

    expect(put).toHaveBeenCalledWith(
      '/api/v1/agent/conversations/c1/snapshot',
      {},
    );
  });

  it('requests server-context pruning only for an edited transcript', async () => {
    await agentApi.syncConversationSnapshot('c1', {
      messages: [{ id: 'u1', role: 'user', content: '保留问题' }],
      pruneAgentContextToMessages: true,
    });

    expect(put).toHaveBeenCalledWith(
      '/api/v1/agent/conversations/c1/snapshot',
      {
        messages: [{ id: 'u1', role: 'user', content: '保留问题' }],
        prune_agent_context_to_messages: true,
      },
    );
  });

  it('drops an obsolete opaque thread snapshot before normalizing a conversation', async () => {
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

    expect(detail.createdAt).toBe('2026-07-26T00:00:00Z');
    expect(detail.threadState).toBeUndefined();
  });

  it('bounds an oversized execution trace before it enters page state', async () => {
    get.mockResolvedValue({
      data: {
        id: 'c1',
        title: '对话',
        title_source: 'auto',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
        messages: [],
        execution_trace: {
          tool_results: Array.from({ length: 160 }, (_, index) => ({
            action_id: `action-${index}`,
            raw_body: 'x'.repeat(12_000),
          })),
        },
      },
    });

    const detail = await agentApi.getConversation('c1');
    const trace = detail.executionTrace;
    const toolResults = trace?.toolResults || [];

    expect(toolResults).toHaveLength(81);
    expect((toolResults[0]?.rawBody as string).length).toBeLessThanOrEqual(1_600);
    expect(trace?.clientTraceTruncated).toBe(true);
    expect(JSON.stringify(trace).length).toBeLessThan(190_000);
  });

  it('preserves citation projections when a long stage history consumes the shared budget', async () => {
    get.mockResolvedValue({
      data: {
        id: 'c1',
        title: '对话',
        title_source: 'auto',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
        messages: [],
        execution_trace: {
          display_parts: [],
          stages: Array.from({ length: 120 }, (_, index) => ({
            event: 'agent_stage',
            stage: 'tool',
            status: 'completed',
            summary: `stage-${index}-${'x'.repeat(900)}`,
            details: { payload: 'x'.repeat(1_200) },
          })),
          tool_results: [{
            action_id: 'action-market',
            tool_name: 'read_market_indices',
            result_summary: '指数行情已返回。',
          }],
          evidence: [{
            evidence_id: 'ev_market0123456789',
            action_id: 'action-market',
          }],
        },
      },
    });

    const detail = await agentApi.getConversation('c1');

    expect(detail.executionTrace?.evidence).toEqual([
      expect.objectContaining({ evidenceId: 'ev_market0123456789' }),
    ]);
  });
});
