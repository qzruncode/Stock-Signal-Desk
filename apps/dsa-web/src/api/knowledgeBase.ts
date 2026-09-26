import apiClient from './index';
import { toCamelCase } from './utils';

export type KnowledgeBaseItem = {
  id: string;
  name: string;
  description: string;
  status: string;
  documentCount: number;
  readyDocumentCount: number;
  createdAt: string | null;
  updatedAt: string | null;
};

export type KnowledgeBaseTask = {
  id: string;
  operation: string;
  status: string;
  stage: string;
  progress: number;
  attempt: number;
  errorCode?: string | null;
  errorDetail?: string | null;
};

export type KnowledgeBaseDocument = {
  id: string;
  knowledgeBaseId: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  contentHash: string;
  status: string;
  pageCount: number;
  textLength: number;
  chunkCount: number;
  activeIndexVersionId?: string | null;
  indexVersion?: number | null;
  task?: KnowledgeBaseTask | null;
  source?: {
    provider: string;
    exchange: string;
    securityCode: string;
    securityName: string;
    reportType: string;
    reportPeriod: string;
    announcementId: string;
    announcementTitle: string;
    publishedAt?: string | null;
    pdfUrl: string;
    announcementUrl?: string;
  } | null;
  errorCode?: string | null;
  errorDetail?: string | null;
  createdAt?: string | null;
};

export type KnowledgeBasePreview = {
  status: string;
  pageCount?: number;
  pages: Array<{
    pageNumber: number;
    text: string;
    textLength: number;
    structure: Array<Record<string, unknown>>;
  }>;
  chunks: Array<{
    id: string;
    chunkIndex: number;
    pageStart: number;
    pageEnd: number;
    section: string;
    text: string;
    metadata: Record<string, unknown>;
  }>;
};

export type KnowledgeBaseSearchResult = {
  citationId: string;
  documentId: string;
  filename: string;
  pageStart: number;
  pageEnd: number;
  section: string;
  snippet: string;
  text: string;
  url: string;
  rrfScore?: number;
};

export type KnowledgeBaseModelServiceResult = {
  success: boolean;
  model: string;
  message: string;
  errorCode?: string | null;
  retryable: boolean;
  latencyMs?: number | null;
  dimension?: number | null;
};

export type KnowledgeBaseModelServiceCheck = {
  success: boolean;
  embedding: KnowledgeBaseModelServiceResult;
  reranker: KnowledgeBaseModelServiceResult;
};

export const knowledgeBaseApi = {
  async testModelServices(): Promise<KnowledgeBaseModelServiceCheck> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/knowledge-bases/model-services/test',
      {},
      { timeout: 120_000 },
    );
    return toCamelCase<KnowledgeBaseModelServiceCheck>(response.data);
  },

  async list(): Promise<KnowledgeBaseItem[]> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/knowledge-bases');
    const payload = toCamelCase<{ items: KnowledgeBaseItem[] }>(response.data);
    return payload.items ?? [];
  },

  async create(payload: { name: string; description?: string }): Promise<KnowledgeBaseItem> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/knowledge-bases', payload);
    return toCamelCase<{ knowledgeBase: KnowledgeBaseItem }>(response.data).knowledgeBase;
  },

  async delete(knowledgeBaseId: string): Promise<void> {
    await apiClient.delete(`/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}`);
  },

  async listDocuments(knowledgeBaseId: string): Promise<KnowledgeBaseDocument[]> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`,
    );
    return toCamelCase<{ items: KnowledgeBaseDocument[] }>(response.data).items ?? [];
  },

  async upload(knowledgeBaseId: string, file: File): Promise<{ document: KnowledgeBaseDocument; duplicate: boolean }> {
    const form = new FormData();
    form.append('file', file, file.name);
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120_000 },
    );
    return toCamelCase<{ document: KnowledgeBaseDocument; duplicate: boolean }>(response.data);
  },

  async retry(documentId: string, rebuildIndex = false): Promise<KnowledgeBaseDocument> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/knowledge-bases/documents/${encodeURIComponent(documentId)}/retry`,
      { rebuild_index: rebuildIndex },
    );
    return toCamelCase<{ document: KnowledgeBaseDocument }>(response.data).document;
  },

  async deleteDocument(documentId: string): Promise<KnowledgeBaseDocument> {
    const response = await apiClient.delete<Record<string, unknown>>(
      `/api/v1/knowledge-bases/documents/${encodeURIComponent(documentId)}`,
    );
    return toCamelCase<{ document: KnowledgeBaseDocument }>(response.data).document;
  },

  async preview(documentId: string, page?: number, offset = 0, limit = 100): Promise<KnowledgeBasePreview> {
    const params = {
      ...(page ? { page } : {}),
      ...(offset > 0 ? { offset } : {}),
      ...(limit !== 100 ? { limit } : {}),
    };
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/knowledge-bases/documents/${encodeURIComponent(documentId)}/preview`,
      { params: Object.keys(params).length ? params : undefined },
    );
    return toCamelCase<KnowledgeBasePreview>(response.data);
  },

  async search(payload: {
    query: string;
    knowledgeBaseIds: string[];
    topK?: number;
  }): Promise<{ results: KnowledgeBaseSearchResult[]; noEvidence: boolean; message?: string }> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/knowledge-bases/search', {
      query: payload.query,
      knowledge_base_ids: payload.knowledgeBaseIds,
      top_k: payload.topK ?? 5,
    }, { timeout: 120_000 });
    return toCamelCase(response.data) as { results: KnowledgeBaseSearchResult[]; noEvidence: boolean; message?: string };
  },
};
