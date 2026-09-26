import { beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgeBaseApi } from '../knowledgeBase';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('../index', () => ({
  default: api,
}));

describe('knowledgeBaseApi', () => {
  beforeEach(() => {
    api.get.mockReset();
    api.post.mockReset();
  });

  it('requests a bounded chunk page through the existing preview endpoint', async () => {
    api.get.mockResolvedValueOnce({ data: { status: 'ready', pages: [], chunks: [] } });

    await knowledgeBaseApi.preview('doc / 1', undefined, 20, 20);

    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/knowledge-bases/documents/doc%20%2F%201/preview',
      { params: { offset: 20, limit: 20 } },
    );
  });

  it('runs a real local model-service check and maps the result to camelCase', async () => {
    api.post.mockResolvedValueOnce({
      data: {
        success: false,
        embedding: {
          success: true,
          model: 'Qwen/Qwen3-Embedding-0.6B@revision',
          dimension: 1024,
          message: 'Embedding 服务正常',
          latency_ms: 48,
        },
        reranker: {
          success: false,
          model: 'Alibaba-NLP/gte-multilingual-reranker-base@revision',
          message: '本地 Reranker 模型服务不可用。',
          error_code: 'network_error',
          retryable: true,
        },
      },
    });

    const result = await knowledgeBaseApi.testModelServices();

    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/knowledge-bases/model-services/test',
      {},
      { timeout: 120_000 },
    );
    expect(result.success).toBe(false);
    expect(result.embedding.latencyMs).toBe(48);
    expect(result.reranker.errorCode).toBe('network_error');
  });

  it('gives hybrid retrieval enough time for local CPU reranking', async () => {
    api.post.mockResolvedValueOnce({ data: { results: [], no_evidence: true } });

    await knowledgeBaseApi.search({
      query: '书中如何定义 AI Agent？',
      knowledgeBaseIds: ['kb-1'],
    });

    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/knowledge-bases/search',
      {
        query: '书中如何定义 AI Agent？',
        knowledge_base_ids: ['kb-1'],
        top_k: 5,
      },
      { timeout: 120_000 },
    );
  });
});
