import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FeedSpec, RssItem } from '../../api/rss';

vi.mock('../../api/rss', async () => {
  const actual = await vi.importActual<typeof import('../../api/rss')>('../../api/rss');
  return {
    ...actual,
    rssApi: {
      ...actual.rssApi,
      getFeedItemDetail: vi.fn(),
      deletePreviewSession: vi.fn().mockResolvedValue(undefined),
    },
  };
});

vi.mock('./PdfViewer', () => ({
  default: ({ resourceUrl }: { resourceUrl: string }) => (
    <div data-testid="pdf-viewer">{resourceUrl}</div>
  ),
}));

const { rssApi } = await import('../../api/rss');
const { RssFeedList } = await import('./RssFeedList');

const SPEC: FeedSpec = {
  route_path: '/szse/disclosure/listed/notice/:query?',
  namespace: 'szse',
  params: {},
  options: {},
};

const ITEM: RssItem = {
  id: 'szse-item',
  title: '惠科股份公告',
  link: 'https://www.szse.cn/disclosure/item',
  summary: '',
  published: null,
  author: '',
  tags: [],
  attachments: [
    {
      url: 'https://disc.static.szse.cn/report.pdf',
      mime_type: 'application/pdf',
    },
  ],
};

describe('RssFeedList document preview', () => {
  beforeEach(() => {
    vi.mocked(rssApi.getFeedItemDetail).mockReset();
    vi.mocked(rssApi.deletePreviewSession).mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('materializes the clicked item and opens its first PDF resource', async () => {
    vi.mocked(rssApi.getFeedItemDetail).mockResolvedValue({
      ...ITEM,
      content_html: '<p>公告正文</p>',
      resources: [
        {
          resource_id: 'textdoc_preview',
          filename: '惠科股份公告.pdf',
          mime_type: 'application/pdf',
          size_bytes: 136333,
          content_hash: 'a'.repeat(64),
          preview_url: '/api/v1/agent/resources/textdoc_preview/content?disposition=inline',
          download_url: '/api/v1/agent/resources/textdoc_preview/content?disposition=attachment',
          extraction_status: 'extracted',
          text_length: 2274,
          chunk_count: 4,
        },
      ],
      document_errors: [],
    });

    const view = render(<RssFeedList items={[ITEM]} spec={SPEC} />);
    fireEvent.click(screen.getByRole('button', { name: '惠科股份公告' }));

    await waitFor(() => expect(rssApi.getFeedItemDetail).toHaveBeenCalledTimes(1));
    const call = vi.mocked(rssApi.getFeedItemDetail).mock.calls[0];
    expect(call[0]).toEqual(SPEC);
    expect(call[1]).toEqual(ITEM);
    expect(call[2]).toMatch(/^[A-Za-z0-9_]+$/);
    expect(await screen.findByText('惠科股份公告.pdf')).toBeInTheDocument();
    expect(await screen.findByTestId('pdf-viewer')).toHaveTextContent(
      '/api/v1/agent/resources/textdoc_preview/content?disposition=inline',
    );
    expect(screen.getByText('订阅源正文')).toBeInTheDocument();
    expect(screen.getByText('公告正文')).not.toBeVisible();
    fireEvent.click(screen.getByText('订阅源正文'));
    expect(screen.getByText('公告正文')).toBeVisible();

    view.unmount();
    await waitFor(() => expect(rssApi.deletePreviewSession).toHaveBeenCalledWith(call[2]));
  });
});
