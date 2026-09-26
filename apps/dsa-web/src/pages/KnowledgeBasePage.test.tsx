import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgeBaseApi } from '../api/knowledgeBase';
import KnowledgeBasePage from './KnowledgeBasePage';

vi.mock('../api/knowledgeBase', () => ({
  knowledgeBaseApi: {
    list: vi.fn(),
    listDocuments: vi.fn(),
    preview: vi.fn(),
    search: vi.fn(),
    retry: vi.fn(),
  },
}));

describe('KnowledgeBasePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(knowledgeBaseApi.list).mockResolvedValue([{
      id: 'kb-1',
      name: 'Ready transition',
      description: '',
      status: 'active',
      documentCount: 1,
      readyDocumentCount: 0,
      createdAt: null,
      updatedAt: null,
    }]);
    vi.mocked(knowledgeBaseApi.listDocuments).mockResolvedValue([{
      id: 'doc-1',
      knowledgeBaseId: 'kb-1',
      filename: 'agent-patterns.pdf',
      mimeType: 'application/pdf',
      sizeBytes: 1024,
      contentHash: 'sha256',
      status: 'ready',
      pageCount: 12,
      textLength: 1200,
      chunkCount: 4,
      activeIndexVersionId: 'index-1',
      indexVersion: 1,
      source: {
        provider: 'RSSHub/深交所',
        exchange: '深交所',
        securityCode: '300850',
        securityName: '新强联',
        reportType: 'semiannual',
        reportPeriod: '2026年半年度',
        announcementId: 'szse-report-1',
        announcementTitle: '新强联：2026年半年度报告',
        publishedAt: '2026-08-29',
        pdfUrl: 'https://disc.static.szse.cn/reports/report.PDF',
        announcementUrl: 'https://www.szse.cn/disclosure/item',
      },
    }]);
    vi.mocked(knowledgeBaseApi.preview).mockResolvedValue({
      status: 'ready',
      pageCount: 1,
      pages: [{ pageNumber: 1, text: '页面原文内容', textLength: 7, structure: [] }],
      chunks: [{ id: 'chunk-1', chunkIndex: 0, pageStart: 1, pageEnd: 1, section: '概述', text: '可检索切片内容', metadata: {} }],
    });
  });

  it('separates create, document detail, chunk preview, and retrieval test flows', async () => {
    render(<KnowledgeBasePage />);
    expect(screen.getByRole('heading', { name: 'PDF 知识库', level: 1 })).toBeInTheDocument();
    expect(screen.queryByText('资料管理与检索验证')).not.toBeInTheDocument();
    expect(screen.queryByText('知识库', { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '检查模型服务' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新知识库状态' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '模型服务' })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(knowledgeBaseApi.listDocuments).toHaveBeenCalledWith('kb-1');
      expect(screen.getByText('1 份文档 · 1 份可检索')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '新建知识库' }));
    expect(await screen.findByRole('dialog', { name: '新建知识库' })).toBeInTheDocument();
    expect(screen.getByLabelText('名称')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));

    fireEvent.click(screen.getByRole('button', { name: '管理' }));
    expect(await screen.findByRole('dialog', { name: 'Ready transition' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /agent-patterns\.pdf1 KB/ }));
    expect(await screen.findByText('可检索切片内容')).toBeInTheDocument();
    expect(screen.getByText('深交所 · 2026年半年度 · RSSHub/深交所')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '官网原件' })).toHaveAttribute(
      'href',
      'https://disc.static.szse.cn/reports/report.PDF',
    );
    fireEvent.click(screen.getByRole('tab', { name: /原文页面/ }));
    expect(screen.getByText('页面原文内容')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭抽屉' }));

    fireEvent.click(screen.getByRole('button', { name: '检索测试：Ready transition' }));
    expect(await screen.findByRole('dialog', { name: '检索测试' })).toBeInTheDocument();
    expect(screen.getByText('知识库 · Ready transition')).toBeInTheDocument();
    expect(screen.queryByLabelText('检索范围')).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('输入问题或关键词'), {
      target: { value: 'Agent 的五步循环是什么？' },
    });

    expect(screen.getByRole('button', { name: '检索' })).toBeEnabled();
    expect(knowledgeBaseApi.listDocuments).toHaveBeenCalledWith('kb-1');
    vi.mocked(knowledgeBaseApi.search).mockResolvedValue({ results: [], noEvidence: false });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));
    await waitFor(() => expect(knowledgeBaseApi.search).toHaveBeenCalledWith({
      query: 'Agent 的五步循环是什么？',
      knowledgeBaseIds: ['kb-1'],
      topK: 5,
    }));
  });

  it('paginates through all document chunks instead of stopping at the first batch', async () => {
    vi.mocked(knowledgeBaseApi.listDocuments).mockResolvedValueOnce([{
      id: 'doc-many',
      knowledgeBaseId: 'kb-1',
      filename: 'long-report.pdf',
      mimeType: 'application/pdf',
      sizeBytes: 2048,
      contentHash: 'sha256-long',
      status: 'ready',
      pageCount: 458,
      textLength: 50_000,
      chunkCount: 21,
      activeIndexVersionId: 'index-long',
      indexVersion: 1,
    }]);
    vi.mocked(knowledgeBaseApi.preview)
      .mockResolvedValueOnce({
        status: 'ready',
        pageCount: 458,
        pages: [{ pageNumber: 1, text: '页面原文内容', textLength: 7, structure: [] }],
        chunks: Array.from({ length: 20 }, (_, chunkIndex) => ({
          id: `chunk-${chunkIndex}`,
          chunkIndex,
          pageStart: chunkIndex + 1,
          pageEnd: chunkIndex + 1,
          section: '正文',
          text: `切片 ${chunkIndex + 1} 的内容`,
          metadata: {},
        })),
      })
      .mockResolvedValueOnce({
        status: 'ready',
        pageCount: 458,
        pages: [],
        chunks: [{ id: 'chunk-20', chunkIndex: 20, pageStart: 21, pageEnd: 21, section: '结论', text: '切片 21 的内容', metadata: {} }],
      });

    render(<KnowledgeBasePage />);
    fireEvent.click(await screen.findByRole('button', { name: '管理' }));
    fireEvent.click(await screen.findByRole('button', { name: /long-report\.pdf2 KB/ }));

    expect(await screen.findByText('切片 1 的内容')).toBeInTheDocument();
    expect(screen.getByText('第 1–20 条 / 共 21 条')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('切片 21 的内容')).toBeInTheDocument();
    expect(screen.getByText('第 21–21 条 / 共 21 条')).toBeInTheDocument();
    expect(knowledgeBaseApi.preview).toHaveBeenLastCalledWith('doc-many', undefined, 20, 20);
  });

  it('paginates original pages beyond the first preview batch', async () => {
    vi.mocked(knowledgeBaseApi.listDocuments).mockResolvedValueOnce([{
      id: 'doc-pages',
      knowledgeBaseId: 'kb-1',
      filename: 'long-report.pdf',
      mimeType: 'application/pdf',
      sizeBytes: 2048,
      contentHash: 'sha256-pages',
      status: 'ready',
      pageCount: 25,
      textLength: 50_000,
      chunkCount: 1,
      activeIndexVersionId: 'index-pages',
      indexVersion: 1,
    }]);
    vi.mocked(knowledgeBaseApi.preview)
      .mockResolvedValueOnce({
        status: 'ready',
        pageCount: 25,
        pages: Array.from({ length: 20 }, (_, index) => ({
          pageNumber: index + 1,
          text: `原文第 ${index + 1} 页内容`,
          textLength: 10,
          structure: [],
        })),
        chunks: [{ id: 'chunk-0', chunkIndex: 0, pageStart: 1, pageEnd: 1, section: '', text: '切片内容', metadata: {} }],
      })
      .mockResolvedValueOnce({
        status: 'ready',
        pageCount: 25,
        pages: Array.from({ length: 5 }, (_, index) => ({
          pageNumber: index + 21,
          text: `原文第 ${index + 21} 页内容`,
          textLength: 11,
          structure: [],
        })),
        chunks: [],
      });

    render(<KnowledgeBasePage />);
    fireEvent.click(await screen.findByRole('button', { name: '管理' }));
    fireEvent.click(await screen.findByRole('button', { name: /long-report\.pdf2 KB/ }));
    fireEvent.click(await screen.findByRole('tab', { name: /原文页面 25/ }));

    expect(await screen.findByText('原文第 1 页内容')).toBeInTheDocument();
    expect(screen.getByText('第 1–20 页 / 共 25 页')).toBeInTheDocument();
    expect(screen.queryByText('原文第 21 页内容')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('原文第 21 页内容')).toBeInTheDocument();
    expect(screen.getByText('第 21–25 页 / 共 25 页')).toBeInTheDocument();
    expect(knowledgeBaseApi.preview).toHaveBeenLastCalledWith('doc-pages', undefined, 20, 20);
  });

  it('clears previous retrieval hits while a new query is pending', async () => {
    vi.mocked(knowledgeBaseApi.list).mockResolvedValueOnce([{
      id: 'kb-1',
      name: 'Ready transition',
      description: '',
      status: 'active',
      documentCount: 1,
      readyDocumentCount: 1,
      createdAt: null,
      updatedAt: null,
    }]);
    const previousResult = {
      citationId: 'old-hit',
      documentId: 'doc-1',
      filename: 'old-report.pdf',
      pageStart: 1,
      pageEnd: 1,
      section: '',
      snippet: '旧命中摘要',
      text: '旧查询命中内容',
      url: '/old-report.pdf#page=1',
    };
    const nextResult = { ...previousResult, citationId: 'new-hit', filename: 'new-report.pdf', text: '新查询命中内容' };
    let resolveNextSearch: (value: Awaited<ReturnType<typeof knowledgeBaseApi.search>>) => void = () => {};
    vi.mocked(knowledgeBaseApi.search)
      .mockResolvedValueOnce({ results: [previousResult], noEvidence: false })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNextSearch = resolve; }));

    render(<KnowledgeBasePage />);
    fireEvent.click(await screen.findByRole('button', { name: '检索测试：Ready transition' }));
    const query = await screen.findByPlaceholderText('输入问题或关键词');
    fireEvent.change(query, { target: { value: '第一次查询' } });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));
    expect(await screen.findByText('旧查询命中内容')).toBeInTheDocument();

    fireEvent.change(query, { target: { value: '第二次查询' } });
    expect(screen.queryByText('旧查询命中内容')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '检索' }));
    expect(await screen.findByRole('status')).toHaveTextContent('正在检索当前知识库…');
    expect(screen.queryByText('旧查询命中内容')).not.toBeInTheDocument();

    resolveNextSearch({ results: [nextResult], noEvidence: false });
    expect(await screen.findByText('新查询命中内容')).toBeInTheDocument();
  });

  it('offers a reprocess action for OCR/text extraction failures', async () => {
    vi.mocked(knowledgeBaseApi.listDocuments).mockResolvedValue([{
      id: 'doc-ocr',
      knowledgeBaseId: 'kb-1',
      filename: 'scanned-report.pdf',
      mimeType: 'application/pdf',
      sizeBytes: 1024,
      contentHash: 'sha256-ocr',
      status: 'unsupported',
      errorCode: 'extraction_quality_low',
      errorDetail: '未从足够多的页面提取到文本。',
      pageCount: 12,
      textLength: 0,
      chunkCount: 0,
      activeIndexVersionId: null,
      indexVersion: null,
    }]);
    vi.mocked(knowledgeBaseApi.retry).mockResolvedValue({
      id: 'doc-ocr',
      knowledgeBaseId: 'kb-1',
      filename: 'scanned-report.pdf',
      mimeType: 'application/pdf',
      sizeBytes: 1024,
      contentHash: 'sha256-ocr',
      status: 'queued',
      pageCount: 12,
      textLength: 0,
      chunkCount: 0,
    });

    render(<KnowledgeBasePage />);
    await waitFor(() => expect(knowledgeBaseApi.listDocuments).toHaveBeenCalledWith('kb-1'));
    fireEvent.click(await screen.findByRole('button', { name: '管理' }));
    expect(await screen.findByRole('button', { name: '重新识别 scanned-report.pdf' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新识别 scanned-report.pdf' }));

    await waitFor(() => expect(knowledgeBaseApi.retry).toHaveBeenCalledWith('doc-ocr', false));
  });
});
