import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import type { FeedSpec, RssItem } from '../../../api/rss';
import RssFeedList from '../../rss/RssFeedList';
import { describeRssEmptyResult, type RssFeedToolResult, type RssFeedItem } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';

function readerSpec(item: RssFeedItem): FeedSpec | null {
  if (!item.rss_route || item.source_type === 'websearch') return null;
  const params = Object.fromEntries(
    Object.entries(item.rss_params ?? {})
      .filter(([, value]) => value != null)
      .map(([key, value]) => [key, String(value)]),
  );
  return { route_path: item.rss_route, params, options: {}, namespace: '' };
}

function readerItem(item: RssFeedItem, index: number): RssItem {
  return {
    id: item.id || item.link || `${item.title}-${index}`,
    title: item.title,
    link: item.link || '',
    summary: item.content_text || item.summary || '',
    published: item.published || null,
    author: item.author || item.source || '',
    tags: item.tags || [],
    image: item.image,
    attachments: item.attachments || [],
  };
}

type RssFeedArgs = { query?: string; topic?: string; include_content?: boolean };

/** One-call semantic RSS aggregation used by search_financial_news. */
const RssFeedToolUI = ({
  toolName,
  args,
  result,
  status,
  isError,
}: ToolCallMessagePartProps<RssFeedArgs, RssFeedToolResult | undefined>) => {
  const isResearchLibrary = toolName === 'search_research_library';
  const runningLabel = args?.include_content ? '正在聚合资讯并读取正文' : '正在聚合财经资讯';
  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} streamingFields={['query', 'topic']} label={runningLabel} />;
  }
  const failed = isError || (status.type === 'incomplete' && status.reason === 'error') || result?.success === false;
  if (failed) {
    return <ToolStatusPill status={status} isError label={isResearchLibrary ? '研究资料检索失败' : '财经资讯获取失败'} />;
  }

  const items = result?.items ?? [];
  if (!result || items.length === 0) {
    return (
      <ToolStatusPill
        status={status}
        label={result ? describeRssEmptyResult(result, isResearchLibrary) : (isResearchLibrary ? '未找到匹配的研究资料' : '未找到相关财经资讯')}
      />
    );
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
          <div key={`${item.link || item.title}-${idx}`} className="min-w-0">
            <RssFeedList items={[readerItem(item, idx)]} spec={readerSpec(item)} />
            {item.content_fallback && <p className="mt-1 px-1 text-[10px] text-amber-600">该来源仅返回摘要</p>}
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
