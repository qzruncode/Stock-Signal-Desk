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

  it('normalizes the typed structured-answer projection for future renderers', async () => {
    get.mockResolvedValue({
      data: {
        id: 'c1',
        title: '对话',
        title_source: 'auto',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
        messages: [],
        execution_trace: {
          structured_answer: {
            profile: 'general',
            title: '结构化说明',
            blocks: [{
              section: '示例',
              kind: 'answer',
              presentation_type: 'code',
              language: 'python',
              content: 'print(1)',
              evidence_ids: [],
            }],
          },
        },
      },
    });

    const detail = await agentApi.getConversation('c1');

    expect(detail.executionTrace?.structuredAnswer).toEqual({
      profile: 'general',
      title: '结构化说明',
      blocks: [{
        section: '示例',
        kind: 'answer',
        presentationType: 'code',
        language: 'python',
        content: 'print(1)',
        evidenceIds: [],
      }],
    });
  });

  it('keeps bounded chart points available to the structured-answer renderer', async () => {
    get.mockResolvedValue({
      data: {
        id: 'c1',
        title: '对话',
        title_source: 'auto',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
        messages: [],
        execution_trace: {
          structured_answer: {
            profile: 'research',
            title: '资金流',
            blocks: [{
              kind: 'fact',
              content: '图表',
              chart_refs: [{
                chart_id: 'chart-1',
                chart_type: 'line',
                title: '主力净流入',
                x_key: 'x',
                series: [{ key: 'main_net_inflow', label: '主力净流入' }],
                data: Array.from({ length: 20 }, (_, index) => ({
                  x: `2026-09-${String(index + 1).padStart(2, '0')}`,
                  main_net_inflow: index * 100,
                })),
              }],
              evidence_ids: [],
            }],
          },
        },
      },
    });

    const detail = await agentApi.getConversation('c1');
    const chart = detail.executionTrace?.structuredAnswer?.blocks[0]?.chartRefs?.[0];

    expect(chart?.data).toHaveLength(20);
    expect(chart?.data[0]).toEqual({ x: '2026-09-01', main_net_inflow: 0 });
    expect(chart?.data[19]).toEqual({ x: '2026-09-20', main_net_inflow: 1900 });
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
