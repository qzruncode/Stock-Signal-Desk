import React, { useMemo, useState } from 'react';
import DOMPurify from 'dompurify';
import { Clock, FileText } from 'lucide-react';
import { rssApi, type FeedSpec, type RssItem, type RssAttachment } from '../../api/rss';
import { Drawer, InlineAlert, Loading } from '../common';

function formatTime(iso: string | null): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 60) return `${diffMin} 分钟前`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour} 小时前`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 30) return `${diffDay} 天前`;
    return d.toLocaleDateString('zh-CN');
  } catch {
    return iso;
  }
}

function isAudio(mime: string): boolean {
  return /^audio\//i.test(mime);
}
function isVideo(mime: string): boolean {
  return /^video\//i.test(mime);
}
function isImage(mime: string): boolean {
  return /^image\//i.test(mime);
}

function AttachmentMedia({ att }: { att: RssAttachment }) {
  if (!isReadableLink(att.url)) return null;
  if (isAudio(att.mime_type)) {
    return <audio controls src={att.url} className="mt-2 w-full" />;
  }
  if (isVideo(att.mime_type)) {
    return <video controls src={att.url} className="mt-2 max-h-64 w-full rounded-lg" />;
  }
  if (isImage(att.mime_type)) {
    return (
      <div className="mt-2">
        <img src={att.url} alt={att.title || ''} className="max-h-64 rounded-lg border border-border" />
      </div>
    );
  }
  return (
    <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-text">
      <FileText className="h-3 w-3" />
      {att.title || `附件 ${att.mime_type || ''}`}
    </span>
  );
}

function isReadableLink(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function SafeHtmlContent({ html }: { html: string }) {
  const cleanHtml = useMemo(
    () => {
      const sanitized = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
      const container = document.createElement('div');
      container.innerHTML = sanitized;
      container.querySelectorAll('a').forEach((anchor) => {
        anchor.replaceWith(...Array.from(anchor.childNodes));
      });
      return container.innerHTML;
    },
    [html],
  );

  return (
    <div
      className="rss-content text-sm leading-7 text-secondary-text"
      dangerouslySetInnerHTML={{ __html: cleanHtml }}
    />
  );
}

export function FeedItemCard({ item, onOpen }: { item: RssItem; onOpen: () => void }) {
  const normalizedTitle = item.title.trim().replace(/\s+/g, ' ');
  const normalizedSummary = item.summary.trim().replace(/\s+/g, ' ');
  const showSummary = Boolean(normalizedSummary && normalizedSummary !== normalizedTitle);

  return (
    <div className="group rounded-xl border border-border bg-card p-4 transition hover:border-cyan/40 hover:shadow-soft-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {/* Image thumbnail */}
          {item.image && (
            <button type="button" onClick={onOpen} className="mb-2 block text-left">
              <img
                src={item.image}
                alt={item.title || ''}
                className="max-h-48 rounded-lg border border-border object-cover"
                onError={(e) => { (e.currentTarget.style.display = 'none'); }}
              />
            </button>
          )}

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

        </div>
      </div>

      {/* Attachments (audio/video/image) */}
      {item.attachments && item.attachments.length > 0 && (
        <div className="mt-2">
          {item.attachments.map((att, idx) => (
            <AttachmentMedia key={`${att.url}-${idx}`} att={att} />
          ))}
        </div>
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
          <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-text">{tag}</span>
        ))}
      </div>
    </div>
  );
}

export interface RssFeedListProps {
  items: RssItem[];
  feedTitle?: string;
  spec?: FeedSpec | null;
}

function FeedItemDetail({ item }: { item: RssItem }) {
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

      {item.image && (
        <img
          src={item.image}
          alt={item.title || ''}
          className="mb-5 max-h-96 w-full rounded-xl border border-border object-contain"
          onError={(e) => { (e.currentTarget.style.display = 'none'); }}
        />
      )}

      {item.content_html ? (
        <SafeHtmlContent html={item.content_html} />
      ) : item.summary ? (
        <p className="whitespace-pre-wrap text-sm leading-7 text-secondary-text">{item.summary}</p>
      ) : (
        <p className="text-sm text-muted-text">该消息源没有提供正文内容。</p>
      )}

      {item.attachments && item.attachments.length > 0 && (
        <div className="mt-5 space-y-2">
          {item.attachments.map((att, idx) => (
            <AttachmentMedia key={`${att.url}-${idx}`} att={att} />
          ))}
        </div>
      )}

    </article>
  );
}

export const RssFeedList: React.FC<RssFeedListProps> = ({ items, feedTitle, spec }) => {
  const [selectedItem, setSelectedItem] = useState<RssItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const openItem = async (item: RssItem) => {
    setSelectedItem(item);
    setDetailError(null);
    // Some flash-news feeds only expose an opaque id and the complete alert
    // text already present in the feed. Without an article URL there is no
    // additional document for RSSHub to resolve.
    if (!spec || !isReadableLink(item.link)) return;

    setDetailLoading(true);
    try {
      setSelectedItem(await rssApi.getFeedItemDetail(spec, item));
    } catch (error) {
      const message =
        (error as { response?: { data?: { detail?: { message?: string } } } })
          ?.response?.data?.detail?.message
        || (error as Error).message
        || '消息正文加载失败';
      setDetailError(message);
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <>
      <div className="space-y-3">
        {feedTitle && (
          <div className="mb-1 flex items-center gap-2 text-xs text-muted-text">
            <span>{feedTitle}</span>
            <span>·</span>
            <span>{items.length} 条</span>
          </div>
        )}
        {items.map((item, idx) => (
          <FeedItemCard
            key={item.id || item.link || `${item.title}-${idx}`}
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
