import React, { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { Clock, Download, FileText } from 'lucide-react';
import apiClient from '../../api';
import { rssApi, type FeedSpec, type RssItem, type TextDocumentResource } from '../../api/rss';
import { Drawer, InlineAlert, Loading } from '../common';

const PdfViewer = lazy(() => import('./PdfViewer'));

type ResourceChunk = {
  chunk_index: number;
  text: string;
  page?: number | null;
  section?: string | null;
};

function formatTime(iso: string | null): string {
  if (!iso) return '';
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const minutes = Math.floor((Date.now() - date.getTime()) / 60_000);
    if (minutes < 60) return `${Math.max(0, minutes)} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days} 天前`;
    return date.toLocaleDateString('zh-CN');
  } catch {
    return iso;
  }
}

function controlledResourceUrl(value: string, resourceId: string): string {
  const expected = `/api/v1/agent/resources/${encodeURIComponent(resourceId)}/content`;
  return value.startsWith(expected) ? value : '';
}

function SafeHtmlContent({ html }: { html: string }) {
  const cleanHtml = useMemo(
    () => DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: [
        'audio',
        'canvas',
        'embed',
        'iframe',
        'img',
        'object',
        'picture',
        'source',
        'svg',
        'video',
      ],
      FORBID_ATTR: ['src', 'srcset', 'poster', 'style'],
    }),
    [html],
  );
  return (
    <div
      className="rss-content text-sm leading-7 text-secondary-text"
      dangerouslySetInnerHTML={{ __html: cleanHtml }}
    />
  );
}

function extractionLabel(resource: TextDocumentResource): string {
  switch (resource.extraction_status) {
    case 'extracted':
      return `${resource.chunk_count} 个文本片段`;
    case 'empty_text_layer':
      return '没有可读取的文本层';
    case 'failed':
      return '文本提取失败';
    case 'unsupported':
      return '不支持的文本格式';
    default:
      return '正在提取文本';
  }
}

function TextDocumentCard({
  resource,
  defaultOpen = false,
}: {
  resource: TextDocumentResource;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [chunks, setChunks] = useState<ResourceChunk[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const previewUrl = controlledResourceUrl(resource.preview_url, resource.resource_id);
  const downloadUrl = controlledResourceUrl(resource.download_url, resource.resource_id);
  const isPdf = resource.mime_type === 'application/pdf'
    || resource.filename.toLowerCase().endsWith('.pdf');

  useEffect(() => {
    if (!open || isPdf || resource.extraction_status !== 'extracted' || chunks.length > 0) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    void (async () => {
      try {
        const collected: ResourceChunk[] = [];
        let offset = 0;
        while (!cancelled) {
          const response = await apiClient.get(
            `/api/v1/agent/resources/${encodeURIComponent(resource.resource_id)}`,
            { params: { offset, limit: 100 } },
          );
          const page = (response.data?.chunks ?? []) as ResourceChunk[];
          collected.push(...page);
          if (!response.data?.has_more || page.length === 0) break;
          offset += page.length;
        }
        if (!cancelled) setChunks(collected);
      } catch (reason: unknown) {
        if (!cancelled) setError((reason as Error)?.message || '文本预览加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chunks.length, isPdf, open, resource.extraction_status, resource.resource_id]);

  return (
    <section className="rounded-xl border border-border bg-muted/25">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => {
            const nextOpen = !open;
            if (
              nextOpen
              && !isPdf
              && resource.extraction_status === 'extracted'
              && chunks.length === 0
            ) {
              setLoading(true);
              setError('');
            }
            setOpen(nextOpen);
          }}
          className="min-w-0 flex-1 text-left"
        >
          <span className="flex items-center gap-2 text-xs font-medium text-foreground">
            <FileText className="size-4 shrink-0 text-cyan-600" />
            <span className="truncate">{resource.filename}</span>
          </span>
          <span className="mt-1 block text-[10px] text-muted-foreground">
            {extractionLabel(resource)}
            {' · '}
            {(resource.size_bytes / 1024).toFixed(resource.size_bytes < 1024 * 1024 ? 0 : 1)} KB
          </span>
        </button>
        {downloadUrl && (
          <a
            href={downloadUrl}
            className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[10px] text-cyan hover:bg-muted"
          >
            <Download className="size-3" />
            原文件
          </a>
        )}
      </div>
      {open && (
        <div className="border-t border-border p-3">
          {isPdf && previewUrl ? (
            <Suspense fallback={<Loading label="正在加载 PDF…" />}>
              <PdfViewer resourceUrl={previewUrl} />
            </Suspense>
          ) : loading ? (
            <Loading label="正在加载提取文本…" />
          ) : error ? (
            <InlineAlert title="文本预览失败" message={error} variant="danger" />
          ) : chunks.length > 0 ? (
            <div className="max-h-[32rem] space-y-3 overflow-y-auto">
              {chunks.map((chunk) => (
                <section key={chunk.chunk_index}>
                  <p className="mb-1 text-[10px] text-muted-foreground">
                    {chunk.page ? `第 ${chunk.page} 页` : chunk.section || `片段 ${chunk.chunk_index + 1}`}
                  </p>
                  <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-6 text-secondary-text">
                    {chunk.text}
                  </pre>
                </section>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {resource.error || extractionLabel(resource)}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export function FeedItemCard({ item, onOpen }: { item: RssItem; onOpen: () => void }) {
  const normalizedTitle = item.title.trim().replace(/\s+/g, ' ');
  const normalizedSummary = item.summary.trim().replace(/\s+/g, ' ');
  const showSummary = Boolean(normalizedSummary && normalizedSummary !== normalizedTitle);
  return (
    <div className="rounded-xl border border-border bg-card p-4 transition hover:border-cyan/40 hover:shadow-soft-card">
      <h3 className="text-sm font-semibold leading-snug text-foreground">
        <button type="button" onClick={onOpen} className="text-left transition-colors hover:text-cyan">
          {item.title || '消息详情'}
        </button>
      </h3>
      {showSummary && (
        <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-secondary-text">
          {item.summary}
        </p>
      )}
      {(item.resources ?? []).length > 0 && (
        <p className="mt-2 text-[10px] text-cyan-700">
          已解析 {item.resources?.length} 个原始文本文件
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-text">
        {item.published && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {formatTime(item.published)}
          </span>
        )}
        {item.author && <span>{item.author}</span>}
        {item.tags.slice(0, 3).map((tag) => (
          <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-[10px]">{tag}</span>
        ))}
      </div>
    </div>
  );
}

function FeedItemRow({ item, onOpen }: { item: RssItem; onOpen: () => void }) {
  const normalizedTitle = item.title.trim().replace(/\s+/g, ' ');
  const normalizedSummary = item.summary.trim().replace(/\s+/g, ' ');
  const accessibleLabel = [normalizedTitle, normalizedSummary, item.author, item.published]
    .filter(Boolean)
    .join(' · ');
  return (
    <div className="min-w-0 border-b border-border/60 last:border-b-0">
      <button
        type="button"
        onClick={onOpen}
        title={accessibleLabel}
        aria-label={accessibleLabel}
        className="flex w-full min-w-0 items-center gap-2 py-1.5 text-left text-[11px] transition-colors hover:bg-muted/40"
      >
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">
          {normalizedTitle || '消息详情'}
        </span>
        {normalizedSummary && normalizedSummary !== normalizedTitle && (
          <span className="min-w-0 flex-[2] truncate text-muted-foreground">{normalizedSummary}</span>
        )}
        <span className="hidden shrink-0 items-center gap-2 text-[10px] text-muted-text sm:flex">
          {item.published && <span>{formatTime(item.published)}</span>}
          {item.author && <span className="max-w-28 truncate">{item.author}</span>}
        </span>
        {(item.resources ?? []).length > 0 && <span className="shrink-0 text-[10px] text-cyan-700">原文</span>}
      </button>
    </div>
  );
}

export interface RssFeedListProps {
  items: RssItem[];
  feedTitle?: string;
  spec?: FeedSpec | null;
  compact?: boolean;
}

function FeedItemDetail({ item }: { item: RssItem }) {
  const resources = item.resources ?? [];
  const hasResourceReader = resources.length > 0;
  const body = item.content_html ? (
    <SafeHtmlContent html={item.content_html} />
  ) : item.summary ? (
    <p className="whitespace-pre-wrap text-sm leading-7 text-secondary-text">{item.summary}</p>
  ) : null;

  return (
    <article>
      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-muted-text">
        {item.published && (
          <span className="flex items-center gap-1">
            <Clock className="h-3.5 w-3.5" />
            {formatTime(item.published)}
          </span>
        )}
        {item.author && <span>{item.author}</span>}
        {item.tags.map((tag) => (
          <span key={tag} className="rounded-full bg-muted px-2 py-0.5">{tag}</span>
        ))}
      </div>
      {hasResourceReader && (
        <div className="space-y-2">
          {resources.map((resource, index) => (
            <TextDocumentCard
              key={resource.resource_id}
              resource={resource}
              defaultOpen={index === 0}
            />
          ))}
        </div>
      )}
      {body && hasResourceReader ? (
        <details className="mt-5 rounded-xl border border-border bg-muted/20 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-foreground">
            订阅源正文
          </summary>
          <div className="mt-3 border-t border-border pt-3">{body}</div>
        </details>
      ) : body ? (
        body
      ) : !hasResourceReader ? (
        <p className="text-sm text-muted-text">该消息源没有提供正文内容。</p>
      ) : null}
      {(item.document_errors ?? []).length > 0 && (
        <div className="mt-5">
          <InlineAlert
            title="部分原文件解析失败"
            message={item.document_errors?.join('；') || ''}
            variant="warning"
          />
        </div>
      )}
    </article>
  );
}

export const RssFeedList: React.FC<RssFeedListProps> = ({ items, feedTitle, spec, compact = false }) => {
  const [previewSessionId] = useState(() => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID().replaceAll('-', '_');
    }
    return `rss_${Date.now()}_${Math.random().toString(36).slice(2, 14)}`;
  });
  const [selectedItem, setSelectedItem] = useState<RssItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailSeqRef = useRef(0);
  const detailAbortRef = useRef<AbortController | null>(null);

  const openItem = async (item: RssItem) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    const sequence = ++detailSeqRef.current;
    setSelectedItem(item);
    setDetailError(null);
    if (!spec || !item.link || item.resources?.length) return;
    setDetailLoading(true);
    try {
      const detail = await rssApi.getFeedItemDetail(
        spec,
        item,
        previewSessionId,
        controller.signal,
      );
      if (sequence === detailSeqRef.current) setSelectedItem(detail);
    } catch (reason: unknown) {
      if (sequence !== detailSeqRef.current) return;
      if (reason instanceof DOMException && reason.name === 'AbortError') return;
      setDetailError((reason as Error)?.message || '消息正文加载失败');
    } finally {
      if (sequence === detailSeqRef.current) setDetailLoading(false);
    }
  };

  useEffect(() => () => detailAbortRef.current?.abort(), []);
  useEffect(
    () => () => {
      void rssApi.deletePreviewSession(previewSessionId).catch(() => undefined);
    },
    [previewSessionId],
  );

  return (
    <>
      <div className={compact ? 'space-y-0' : 'space-y-3'}>
        {feedTitle && (
          <div className="mb-1 flex items-center gap-2 text-xs text-muted-text">
            <span>{feedTitle}</span>
            <span>·</span>
            <span>{items.length} 条</span>
          </div>
        )}
        {items.map((item, index) => compact ? (
          <FeedItemRow
            key={item.id || item.link || `${item.title}-${index}`}
            item={item}
            onOpen={() => void openItem(item)}
          />
        ) : (
          <FeedItemCard
            key={item.id || item.link || `${item.title}-${index}`}
            item={item}
            onOpen={() => void openItem(item)}
          />
        ))}
      </div>
      <Drawer
        isOpen={Boolean(selectedItem)}
        onClose={() => setSelectedItem(null)}
        title={selectedItem?.title || '消息详情'}
        width="max-w-3xl"
      >
        {detailLoading && <Loading label="正在加载消息内容…" />}
        {detailError && (
          <div className="mb-4">
            <InlineAlert title="正文加载失败" message={detailError} variant="danger" />
          </div>
        )}
        {!detailLoading && selectedItem && <FeedItemDetail item={selectedItem} />}
      </Drawer>
    </>
  );
};

export default RssFeedList;
