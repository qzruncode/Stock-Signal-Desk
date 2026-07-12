import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { ExternalLinkIcon } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { type RssFeedToolResult, type RssFeedItem, type RssItemToolResult } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';

function formatDateTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd} ${hh}:${mi}`;
}

type RssFeedArgs = { route_path?: string; params?: Record<string, unknown>; keyword?: string; title?: string };
type RssResult = RssFeedToolResult | RssItemToolResult | undefined;

function isItemResult(r: RssResult): r is RssItemToolResult {
  return !!r && typeof r === 'object' && 'content_text' in r;
}

/**
 * RSS 工具内联可视化。同时服务 read_rss_feed(条目列表) 与 read_rss_item(单条全文):
 * 根据 result 形态分发——列表渲染卡片,全文渲染正文块。
 */
const RssFeedToolUI = ({
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<RssFeedArgs, RssResult>) => {
  const label = args?.title ? '正在读取资讯全文' : '正在读取 RSS 资讯';

  if (status.type === 'running' && !result) {
    return (
      <ToolStatusPill
        status={status}
        isError={isError}
        streamingFields={args?.title ? ['title'] : ['route_path']}
        label={label}
      />
    );
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="读取 RSS 失败" />;
  }

  // ── read_rss_item:单条全文 ──
  if (isItemResult(result)) {
    if (result._not_found) {
      return <ToolStatusPill status={status} isError label="未找到该资讯全文" />;
    }
    return (
      <div className="my-2 w-full min-w-0 space-y-2">
        <div className="rounded-lg border border-border bg-card/60 px-3 py-2.5">
          <div className="text-sm font-medium text-foreground [overflow-wrap:anywhere]">{result.title}</div>
          <div className="mt-2 whitespace-pre-wrap break-words text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">
            {result.content_text || '（无正文）'}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground/80">
            {result.published && <span>{formatDateTime(result.published)}</span>}
            {result.source && <span>{result.source}</span>}
            {result._fallback && <span className="text-amber-600">· 列表摘要回退</span>}
            {result._truncated && <span className="text-amber-600">· 正文已截断</span>}
            {result.link && (
              <a href={result.link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-cyan-600 hover:underline">
                原文 <ExternalLinkIcon className="size-3" />
              </a>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── read_rss_feed:条目列表 ──
  const items = result?.items ?? [];
  if (!result || items.length === 0) {
    return <ToolStatusPill status={status} isError label="无 RSS 资讯" />;
  }
  return (
    <div className="my-2 w-full min-w-0 space-y-2">
      {result.feed_title && (
        <div className="flex w-full min-w-0 flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-[11px]">
          <span className="font-medium text-foreground">{result.feed_title}</span>
          {result._cached && <span className="text-muted-foreground">· 缓存</span>}
        </div>
      )}
      <div className="space-y-1.5">
        {items.map((item: RssFeedItem, idx) => (
          <div key={idx} className="w-full min-w-0 rounded-lg border border-border bg-card/60 px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 text-sm font-medium text-foreground [overflow-wrap:anywhere]">{item.title}</span>
            </div>
            {item.summary && <p className="mt-1 text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">{item.summary}</p>}
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground/80">
              {item.published && <span>{formatDateTime(item.published)}</span>}
              {item.source && <span>{item.source}</span>}
              {item.link && (
                <a href={item.link} target="_blank" rel="noopener noreferrer" className={cn('inline-flex items-center gap-0.5 text-cyan-600 hover:underline')}>
                  原文 <ExternalLinkIcon className="size-3" />
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
      {result.item_count != null && result.item_count > items.length && (
        <p className="text-[11px] text-muted-foreground">共 {result.item_count} 条,已展示前 {items.length} 条</p>
      )}
    </div>
  );
};

export default RssFeedToolUI;
