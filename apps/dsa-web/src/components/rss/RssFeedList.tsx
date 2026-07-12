import React, { lazy, Suspense, useMemo, useState } from 'react';
import DOMPurify from 'dompurify';
import { Clock, FileText } from 'lucide-react';
import { rssApi, type FeedSpec, type RssItem, type RssAttachment } from '../../api/rss';
import { Drawer, InlineAlert, Loading } from '../common';

// pdfjs-dist 体积大，懒加载到独立 chunk，仅在打开 PDF 类 item 时加载。
const PdfViewer = lazy(() => import('./PdfViewer'));

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

/**
 * Whether a link points at a binary document (PDF / Office / zip / …) rather
 * than an HTML article. Such items have no extractable article body — RSSHub's
 * `mode=fulltext` re-fetch can't pull prose from a PDF, and the upstream often
 * returns empty on the filtered re-fetch (yielding 404 "未找到该消息内容"). For
 * these, we skip the fulltext detail fetch and offer an "open original" link
 * instead of showing a misleading load error.
 */
const BINARY_DOC_PATTERN = /\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|epub|mobi|rtf|csv)(?:$|\?|#)/i;
function isBinaryDocLink(value: string): boolean {
  if (!value) return false;
  try {
    const url = new URL(value);
    // Path-based detection (e.g. ...&filetitle=foo.pdf) plus an explicit
    // attachment content-type if the feed provided one.
    return BINARY_DOC_PATTERN.test(url.pathname) || BINARY_DOC_PATTERN.test(url.search);
  } catch {
    return BINARY_DOC_PATTERN.test(value);
  }
}

const PDF_PATTERN = /\.pdf(?:$|\?|#)/i;
/**
 * Whether a link points at a PDF we can render inline. nanhua's PDF URL keeps
 * the `.pdf` extension inside the `filetitle=` query param (not the path), so
 * we check both pathname and search; also match the `getReportFile` endpoint
 * for robustness.
 */
function isPdfLink(value: string): boolean {
  if (!value) return false;
  try {
    const url = new URL(value);
    return (
      PDF_PATTERN.test(url.pathname)
      || PDF_PATTERN.test(url.search)
      || /getReportFile/i.test(url.pathname)
    );
  } catch {
    return PDF_PATTERN.test(value);
  }
}

/** Build the same-origin proxy URL for PDF.js to fetch (avoids CORS + forces inline). */
function buildPdfProxyUrl(pdfUrl: string): string {
  return `/api/v1/rss/pdf/proxy?url=${encodeURIComponent(pdfUrl)}`;
}

/**
 * Whether an attachment is a PDF — by explicit mime_type or a `.pdf` URL. Some
 * feeds (e.g. /chinaratings/CreditResearch) attach the actual report as a PDF
 * while `item.link` points at an HTML shell with no extractable body; we render
 * that attachment inline via PDF.js instead of leaving a dead "no content" view.
 */
function isPdfAttachment(att: RssAttachment): boolean {
  if (/^application\/pdf/i.test(att.mime_type || '')) return true;
  return isPdfLink(att.url);
}

/**
 * Extract the first PDF URL from an item's HTML body. A third PDF-report shape
 * (e.g. /wkjyqh/research) embeds the actual report as an `<a href="...pdf">`
 * inside `content_html` — `item.link` is an HTML shell and `attachments` is
 * empty, so neither `isPdfLink(link)` nor the attachment scan finds it. We only
 * call this when the body is trivial (see `isTrivialBody`), so a normal article
 * that merely mentions a PDF attachment isn't mistaken for a PDF-only item.
 */
function extractPdfUrlFromHtml(html: string): string {
  if (!html) return '';
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const anchors = doc.querySelectorAll('a[href]');
    for (const a of Array.from(anchors)) {
      const href = a.getAttribute('href') || '';
      if (href && isPdfLink(href)) return href;
    }
    return '';
  } catch {
    return '';
  }
}

// RSSHub fulltext re-fetch on SPA/PDF-shell pages yields an empty container
// (e.g. chinaratings fulltext: `<div class="main ratingCertification"> </div>`),
// and the list-mode body is often just the title echoed back. Detect those, plus
// the wkjyqh shape where the body is a PDF filename (usually `<title>.pdf`), so
// an attachment/link-only item isn't mistaken for one with real prose.
function isTrivialBody(html: string, title: string): boolean {
  const stripped = html.replace(/<[^>]+>/g, '').trim();
  if (stripped.length === 0) return true;
  const t = (title || '').trim();
  if (t.length > 0 && stripped === t) return true;
  // PDF-report "fake body": strip-tag yields just a .pdf filename (often the
  // title with a .pdf suffix). Doesn't match nanhua/chinaratings whose bodies
  // aren't .pdf-suffixed.
  return t.length > 0 && /\.pdf$/i.test(stripped) && stripped.replace(/\.pdf$/i, '').trim() === t;
}

/**
 * Find the first PDF to render inline for an item: the link itself if it's a
 * PDF (nanhua-style), otherwise the first PDF attachment (chinaratings-style),
 * otherwise — when the body is just a PDF filename echo — the PDF link embedded
 * in `content_html` (wkjyqh-style). Returns '' when the item has no PDF at all.
 */
function findInlinePdfUrl(item: RssItem): string {
  if (item.link && isPdfLink(item.link)) return item.link;
  const pdfAtt = (item.attachments || []).find(isPdfAttachment);
  if (pdfAtt) return pdfAtt.url;
  const html = item.content_html || '';
  if (isTrivialBody(html, item.title)) {
    return extractPdfUrlFromHtml(html);
  }
  return '';
}

/**
 * Whether an item's content lives in a binary document rather than an HTML
 * article — either the link is a binary file, or it carries a PDF/Office
 * attachment while the HTML body is empty/trivial, or the only real content is
 * a PDF link embedded in a trivial body (wkjyqh-style). Such items skip the
 * fulltext re-fetch (the HTML shell has no extractable body) and render the
 * file inline.
 */
function itemIsBinaryDoc(item: RssItem): boolean {
  if (item.link && isBinaryDocLink(item.link)) return true;
  const hasPdfAtt = Boolean((item.attachments || []).some(isPdfAttachment));
  // Only treat an attachment-only item as binary when there's no real article
  // body — a feed that ships both HTML prose and a PDF attachment should still
  // show the prose (and the attachment), not jump straight to the PDF viewer.
  const hasBody = Boolean((item.content_html || '').trim()) && !isTrivialBody(item.content_html || '', item.title);
  if (hasPdfAtt && !hasBody) return true;
  // Third shape: the PDF lives in a `<a href="...pdf">` inside a trivial body.
  if (!hasBody && extractPdfUrlFromHtml(item.content_html || '')) return true;
  return false;
}

function upgradeInsecureMediaSrc(container: HTMLElement): void {
  // RSS item bodies sometimes embed `<img src="http://...">` / `<video src="http://...">`
  // from upstream CDNs that publish over plain HTTP. On an HTTPS page the browser's
  // mixed-content policy silently blocks these, so the media shows broken. The same
  // CDNs typically serve over HTTPS too, so upgrade every src/poster before render.
  container.querySelectorAll('img, video, audio, source').forEach((el) => {
    const upgrade = (attr: string) => {
      const val = el.getAttribute(attr);
      if (val && val.startsWith('http://')) {
        el.setAttribute(attr, `https://${val.slice('http://'.length)}`);
      }
    };
    upgrade('src');
    upgrade('poster');
  });
}

function SafeHtmlContent({ html }: { html: string }) {
  const cleanHtml = useMemo(
    () => {
      const sanitized = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
      const container = document.createElement('div');
      container.innerHTML = sanitized;
      upgradeInsecureMediaSrc(container);
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
  // PDF reports render inline via PDF.js (lazy chunk). The PDF may be the item
  // link itself (nanhua-style), an attachment on an HTML-shell item
  // (chinaratings-style), or a link embedded in a trivial body (wkjyqh-style).
  const inlinePdf = findInlinePdfUrl(item);
  // When the body is just a PDF filename echo and we're rendering the PDF
  // inline, skip the body — it would only repeat the filename as plain text
  // (its <a> is stripped by SafeHtmlContent) and clutter the view above the
  // already-rendered report.
  const pdfOnly = Boolean(inlinePdf) && isTrivialBody(item.content_html || '', item.title);

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

      {!pdfOnly && (
        item.content_html ? (
          <SafeHtmlContent html={item.content_html} />
        ) : item.summary ? (
          <p className="whitespace-pre-wrap text-sm leading-7 text-secondary-text">{item.summary}</p>
        ) : (
          <p className="text-sm text-muted-text">该消息源没有提供正文内容。</p>
        )
      )}

      {inlinePdf ? (
        <div className="mt-5">
          <Suspense fallback={<Loading label="正在加载 PDF 渲染器…" />}>
            <PdfViewer proxyUrl={buildPdfProxyUrl(inlinePdf)} />
          </Suspense>
        </div>
      ) : item.link && isBinaryDocLink(item.link) ? (
        // Other binary documents (Office/zip/…) have no extractable body, so
        // offer the original file instead of a dead-end "no content" line.
        <a
          href={item.link}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-5 inline-flex items-center gap-1.5 rounded-xl border border-border bg-muted/40 px-4 py-2.5 text-sm text-cyan transition-colors hover:bg-muted/70"
        >
          <FileText className="h-4 w-4" />
          打开原文件（PDF / 文档）
        </a>
      ) : null}

      {/* Non-PDF attachments (audio/video/image) still render as media. PDF
          attachments are already shown via the inline viewer above, so skip
          them here to avoid a duplicate. */}
      {item.attachments && item.attachments.length > 0 && (
        <div className="mt-5 space-y-2">
          {item.attachments
            .filter((att) => !isPdfAttachment(att))
            .map((att, idx) => (
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
    // Binary documents (PDF/Office/…) have no extractable article body — this
    // includes items whose link is a binary file *and* items that carry a PDF
    // attachment on an empty HTML shell (e.g. chinaratings). The fulltext
    // re-fetch can't pull prose from these and commonly returns an empty shell;
    // skip it and render the file/PDF inline instead.
    if (itemIsBinaryDoc(item)) return;

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
