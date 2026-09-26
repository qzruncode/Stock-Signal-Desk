import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AssistantMarkdown, AssistantMarkdownText } from './AssistantMarkdownText';

vi.mock('../rss/PdfViewer', () => ({
  default: ({ resourceUrl, initialPage }: { resourceUrl: string; initialPage?: number }) => (
    <div data-testid="pdf-viewer" data-page={initialPage}>{resourceUrl}</div>
  ),
}));

describe('AssistantMarkdown evidence citations', () => {
  it('reveals live assistant text progressively before settling on the full sentence', async () => {
    const text = '我会先核对证券身份，再继续读取最新行情。';

    render(<AssistantMarkdown text={text} animate />);

    expect(screen.queryByText(text)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(text)).toBeInTheDocument());
  });

  it('does not replay a completed sentence when a live projection is replaced', async () => {
    const firstText = '我会先核对证券身份，再继续读取最新行情。';
    const replacementText = '我会先核对证券身份，然后汇总最新行情。';
    const view = render(<AssistantMarkdown text={firstText} animate />);

    await waitFor(() => expect(screen.getByText(firstText)).toBeInTheDocument());
    view.rerender(<AssistantMarkdown text={replacementText} animate />);

    await waitFor(() => expect(screen.getByText(replacementText)).toBeInTheDocument());
    expect(screen.queryByText(firstText)).not.toBeInTheDocument();
  });

  it('hides the raw ID and reveals the evidence summary on hover', async () => {
    render(
      <AssistantMarkdown
        text="指数已经回稳【ev_market】。"
        evidence={{
          evidence: [{ evidence_id: 'ev_market0123456789', action_id: 'market-1' }],
          tool_results: [{
            action_id: 'market-1',
            tool_name: 'read_market_indices',
            result_summary: '已返回主要指数行情。',
            source_labels: ['新浪财经'],
            result_items: [{
              title: '上证指数',
              attributes: [{ name: 'price', value: '3942.0879' }],
            }],
          }],
        }}
      />,
    );

    expect(screen.getByText('①')).toBeInTheDocument();
    expect(screen.getByText('①')).not.toHaveClass('rounded-full');
    expect(screen.getByLabelText('查看证据 ①')).toHaveClass('cursor-help');
    expect(screen.queryByText(/ev_market/)).not.toBeInTheDocument();

    fireEvent.pointerEnter(screen.getByLabelText('查看证据 ①'));
    await waitFor(() => {
      const tooltip = screen.getByRole('tooltip');
      expect(tooltip).toHaveTextContent('参考内容 · ①');
      expect(tooltip).toHaveTextContent('已返回主要指数行情。');
      expect(tooltip).toHaveTextContent('新浪财经');
      expect(tooltip).toHaveTextContent('最新价：3942.0879');

      const metadata = tooltip.querySelector('[data-evidence-metadata]');
      expect(metadata).not.toBeNull();
      expect(metadata).toHaveTextContent('工具：read_market_indices');
      expect(metadata).toHaveClass('text-[10px]', 'text-muted-foreground');
    });
  });

  it('opens a same-origin PDF citation in the in-app viewer at its verified page and rejects external citation URLs', async () => {
    const citationPath = '/api/v1/knowledge-bases/documents/doc-42/content#page=7';
    render(
      <AssistantMarkdown
        text="报告指出主营业务包含工业视觉检测【ev_pdf】。"
        evidence={{
          evidence: [{ evidence_id: 'ev_pdf', action_id: 'pdf-search' }],
          tool_results: [{
            action_id: 'pdf-search',
            tool_name: 'search_knowledge_base',
            result_items: [
              { title: 'annual.pdf · 第 7 页', summary: '主营业务包括工业视觉检测。', url: citationPath },
              {
                title: 'chunk_id=chunk-8 · document_id=doc-42 · knowledge_base_id=kb-1',
                summary: '第二段原文。',
                url: '/api/v1/knowledge-bases/documents/doc-42/content#page=8',
              },
              { title: 'untrusted.pdf', summary: '不能把外链作为可点击的知识库引用。', url: 'https://example.com/evil.pdf#page=1' },
            ],
          }],
        }}
      />,
    );

    fireEvent.pointerEnter(screen.getByLabelText('查看证据 ①'));
    const tooltip = await screen.findByRole('tooltip');
    const openButton = screen.getByRole('button', { name: '在 PDF 中打开 annual.pdf · 第 7 页' });
    expect(tooltip).toHaveTextContent('主营业务包括工业视觉检测。');
    expect(screen.getByRole('button', { name: '在 PDF 中打开 PDF 原文 · 第 8 页' })).toBeInTheDocument();
    expect(tooltip).not.toHaveTextContent('chunk_id=chunk-8');
    expect(screen.queryByRole('button', { name: /untrusted\.pdf/ })).not.toBeInTheDocument();

    fireEvent.click(openButton);
    expect(await screen.findByRole('dialog')).toHaveTextContent('原文第 7 页');
    await waitFor(() => {
      const viewer = screen.getByTestId('pdf-viewer');
      expect(viewer).toHaveAttribute('data-page', '7');
      expect(viewer).toHaveTextContent('/api/v1/knowledge-bases/documents/doc-42/content');
    });
  });

  it('keeps native answer parts readable when their trace arrives as metadata', () => {
    render(
      <AssistantMarkdownText
        type="text"
        status={{ type: 'complete' }}
        text="行情已核验【证据 ev_native】。"
        evidence={{
          evidence: [{ evidence_id: 'ev_native', action_id: 'native-action' }],
          tool_results: [{
            action_id: 'native-action',
            tool_name: 'read_realtime_quote',
            result_summary: '已返回最新行情。',
          }],
        }}
      />,
    );

    expect(screen.getByText('①')).toBeInTheDocument();
    expect(screen.queryByText(/ev_native/)).not.toBeInTheDocument();
  });

  it('does not expose an unresolved evidence ID in the chat projection', () => {
    render(
      <AssistantMarkdown
        text="部分结论暂时无法关联来源【证据 ev_missing】。"
        evidence={{ evidence: [] }}
      />,
    );

    expect(screen.queryByText(/ev_missing/)).not.toBeInTheDocument();
    expect(screen.getByText(/部分证据暂未关联/)).toBeInTheDocument();
  });
});
