import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { ExternalLinkIcon } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { type RssFeedToolResult, type RssFeedItem } from '../../../utils/toolResults';
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

type RssFeedArgs = { query?: string; topic?: string; include_content?: boolean };

/** One-call semantic RSS aggregation used by search_financial_news. */
const RssFeedToolUI = ({
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<RssFeedArgs, RssFeedToolResult | undefined>) => {
  const runningLabel = args?.include_content ? '正在聚合资讯并读取正文' : '正在聚合财经资讯';
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} streamingFields={['query', 'topic']} label={runningLabel} />;
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="财经资讯获取失败" />;
  }

  const items = result?.items ?? [];
  if (!result || items.length === 0) {
    return <ToolStatusPill status={status} isError label="未找到相关财经资讯" />;
  }
  return (
    <div className="my-2 w-full min-w-0 space-y-2">
      {result.query && (
        <div className="flex w-full min-w-0 flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-[11px]">
          <span className="font-medium text-foreground">{result.query}</span>
          {result.topic && <span className="text-muted-foreground">· {result.topic}</span>}
          {result.fallback_used && <span className="text-amber-600">· 已启用网页兜底</span>}
        </div>
      )}
      <div className="space-y-1.5">
        {items.map((item: RssFeedItem, idx) => (
          <div key={`${item.link || item.title}-${idx}`} className="w-full min-w-0 rounded-lg border border-border bg-card/60 px-3 py-2">
            <div className="text-sm font-medium text-foreground [overflow-wrap:anywhere]">{item.title}</div>
            {item.summary && <p className="mt-1 text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">{item.summary}</p>}
            {item.content_text && <p className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">{item.content_text}</p>}
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground/80">
              {item.published && <span>{formatDateTime(item.published)}</span>}
              {item.source && <span>{item.source}</span>}
              {item.content_fallback && <span className="text-amber-600">· 摘要回退</span>}
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
        <p className="text-[11px] text-muted-foreground">共 {result.item_count} 条，已展示前 {items.length} 条</p>
      )}
    </div>
  );
};

export default RssFeedToolUI;
