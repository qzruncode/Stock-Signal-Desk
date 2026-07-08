import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { cn } from '../../../utils/cn';
import { formatNum, type NewsToolResult, type NewsToolItem } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';

function polarityTone(polarity?: string): string {
  if (!polarity) return 'text-muted-foreground bg-muted';
  if (polarity.includes('正') || polarity.toLowerCase().includes('pos')) return 'text-red-600 bg-red-50';
  if (polarity.includes('负') || polarity.toLowerCase().includes('neg')) return 'text-emerald-600 bg-emerald-50';
  return 'text-muted-foreground bg-muted';
}

function polarityLabel(polarity?: string): string {
  if (!polarity) return '中性';
  if (polarity.includes('正') || polarity.toLowerCase().includes('pos')) return '正面';
  if (polarity.includes('负') || polarity.toLowerCase().includes('neg')) return '负面';
  return '中性';
}

function formatDate(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/**
 * 新闻/舆情内联列表。
 */
const NewsToolUI = ({
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<{ symbol: string; days?: number }, NewsToolResult>) => {
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} label={`正在搜索 ${args.symbol} 相关新闻…`} />;
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="搜索新闻失败" />;
  }

  const items = result?.items ?? [];
  if (!result || items.length === 0) {
    return <ToolStatusPill status={status} isError label="无相关新闻" />;
  }

  return (
    <div className="my-2 space-y-2">
      {(result.sentiment_score != null || result.overall_score != null) && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-1.5 text-[11px]">
          {result.overall_score != null && <span className="text-muted-foreground">综合评分 <b className="text-foreground">{formatNum(result.overall_score)}</b></span>}
          {result.sentiment_score != null && <span className="text-muted-foreground">情感分 <b className="text-foreground">{formatNum(result.sentiment_score)}</b></span>}
          {result.positive_count != null && <span className="text-red-600">正面 {result.positive_count}</span>}
          {result.negative_count != null && <span className="text-emerald-600">负面 {result.negative_count}</span>}
          {result.neutral_count != null && <span className="text-muted-foreground">中性 {result.neutral_count}</span>}
        </div>
      )}
      <div className="space-y-1.5">
        {items.map((item: NewsToolItem, idx) => (
          <div key={idx} className="rounded-lg border border-border bg-card/60 px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 text-sm font-medium text-foreground [overflow-wrap:anywhere]">{item.title}</span>
              {item.polarity != null && (
                <span className={cn('shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium', polarityTone(item.polarity))}>
                  {polarityLabel(item.polarity)}
                </span>
              )}
            </div>
            {item.summary && <p className="mt-1 text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">{item.summary}</p>}
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground/80">
              {item.publish_time && <span>{formatDate(item.publish_time)}</span>}
              {item.source && <span>{item.source}</span>}
              {item.category && <span>· {item.category}</span>}
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

export default NewsToolUI;
