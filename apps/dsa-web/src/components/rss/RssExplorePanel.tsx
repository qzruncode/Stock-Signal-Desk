import React, { useMemo, useState } from 'react';
import { RefreshCw, Search, Star, Loader2 } from 'lucide-react';
import type { RssRouteDescriptor, RssFeedOptions, FeedSpec } from '../../api/rss';
import { Button, EmptyState, InlineAlert, Loading, Badge } from '../common';
import { cn } from '../../utils/cn';
import { useRssFeeds } from '../../hooks/useRssFeeds';
import { useInfiniteScroll } from '../../hooks/useInfiniteScroll';
import { RssRouteParamForm } from './RssRouteParamForm';
import { RssOptionsPanel } from './RssOptionsPanel';
import { RssFeedList } from './RssFeedList';
import { RssDownloadMenu } from './RssDownloadMenu';
import { paramsFromExample, parseRouteParams, requiresAuth } from '../../utils/rssRoute';

const EMPTY_OPTIONS: RssFeedOptions = {};
const PAGE_SIZE = 80;

export interface RssExplorePanelProps {
  routes: RssRouteDescriptor[];
  loading: boolean;
  error: string | null;
  search: string;
  setSearch: (v: string) => void;
  filtered: RssRouteDescriptor[];
  onReload: () => Promise<void> | void;
  categories: string[];
  category: string;
  setCategory: (v: string) => void;
  /** True when the route list came from a stale cache (RSSHub instance was unavailable). */
  stale: boolean;
}

export const RssExplorePanel: React.FC<RssExplorePanelProps> = ({
  loading, error, search, setSearch, filtered, onReload,
  categories, category, setCategory, stale,
}) => {
  const pickCategory = (next: string) => {
    setCategory(next);
    setVisibleCount(PAGE_SIZE);
  };
  const [selected, setSelected] = useState<RssRouteDescriptor | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [options, setOptions] = useState<RssFeedOptions>(EMPTY_OPTIONS);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reloading, setReloading] = useState(false);

  // Effective selection: once the user picks a route it stays selected even when
  // a search/category filter hides it from the list — silently swapping the
  // right pane to filtered[0] would be confusing ("I didn't click another
  // route"). Before any pick, default to the first visible route so the pane
  // isn't empty on first load. Derived (not an effect) to avoid setState-in-effect.
  const effectiveSelected: RssRouteDescriptor | null = selected ?? filtered[0] ?? null;

  const hasMore = !loading && !error && visibleCount < filtered.length;
  const { sentinelRef } = useInfiniteScroll({
    hasMore,
    loadingMore,
    loading,
    onLoadMore: () => {
      setLoadingMore(true);
      // Defer to next tick so the sentinel can re-observe after the list grows.
      setTimeout(() => {
        setVisibleCount((c) => c + PAGE_SIZE);
        setLoadingMore(false);
      }, 0);
    },
  });

  const handleSelect = (route: RssRouteDescriptor) => {
    setSelected(route);
    setParams(paramsFromExample(route.route_path, route.example));
    setOptions(EMPTY_OPTIONS);
  };

  const spec: FeedSpec | null = useMemo(() => {
    if (!effectiveSelected) return null;
    return {
      route_path: effectiveSelected.route_path,
      namespace: effectiveSelected.namespace,
      params,
      options,
    };
  }, [effectiveSelected, params, options]);

  const specReady = useMemo(() => {
    if (!effectiveSelected) return false;
    return parseRouteParams(effectiveSelected.route_path)
      .every((param) => param.optional || Boolean(params[param.name]?.trim()));
  }, [effectiveSelected, params]);

  const { feedData, loading: feedLoading, error: feedError, fetchFeeds } = useRssFeeds(spec, specReady);

  const handleReload = () => {
    if (reloading) return;
    setReloading(true);
    void Promise.resolve(onReload()).finally(() => setReloading(false));
  };

  const visible = filtered.slice(0, visibleCount);

  return (
    <div className="grid h-full grid-cols-1 gap-3 lg:grid-cols-[300px_1fr]">
      {/* Left: route list */}
      <div className="flex min-h-0 flex-col rounded-xl border border-border bg-card">
        <div className="shrink-0 border-b border-border p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-text" />
            <input
              type="text"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setVisibleCount(PAGE_SIZE); }}
              placeholder="搜索路由 / 命名空间"
              className="h-8 w-full rounded-md border border-border bg-input-surface pl-8 pr-2 text-xs text-foreground placeholder:text-muted-text focus:border-cyan focus:outline-none focus:ring-2 focus:ring-cyan/20"
            />
          </div>
          <div className="mt-1.5 flex items-center justify-between px-0.5 text-[10px] text-muted-text">
            <span className="flex items-center gap-1.5">
              {loading ? '加载中…' : `${filtered.length} 条股市路由`}
              {stale && !loading && <Badge variant="warning">缓存可能过期</Badge>}
            </span>
            <button
              type="button"
              onClick={handleReload}
              disabled={reloading}
              className="inline-flex items-center gap-1 hover:text-foreground disabled:opacity-50"
            >
              {reloading && <Loader2 className="h-3 w-3 animate-spin" />}
              {reloading ? '刷新中…' : '刷新列表'}
            </button>
          </div>
          {!loading && categories.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1 px-0.5">
              <CategoryChip
                active={category === ''}
                onClick={() => pickCategory('')}
                label="全部"
              />
              {categories.map((cat) => (
                <CategoryChip
                  key={cat}
                  active={category === cat}
                  onClick={() => pickCategory(cat)}
                  label={cat}
                />
              ))}
            </div>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar p-1.5">
          {loading && <div className="p-4"><Loading label="加载路由…" /></div>}
          {!loading && error && <div className="p-3 text-xs text-danger">{error}</div>}
          {!loading && !error && visible.length === 0 && (
            <div className="p-4 text-center text-xs text-muted-text">未找到匹配路由</div>
          )}
          {!loading && !error && visible.map((route) => {
            const active = effectiveSelected?.route_path === route.route_path;
            return (
              <button
                key={route.route_path}
                type="button"
                onClick={() => handleSelect(route)}
                className={cn(
                  'mb-1 block w-full rounded-md border p-2 text-left transition',
                  active ? 'border-cyan/60 bg-cyan/5' : 'border-transparent hover:bg-muted/60',
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className={cn('truncate text-xs font-medium', active ? 'text-cyan' : 'text-foreground')}>
                    {route.name || route.route_path}
                  </span>
                  {requiresAuth(route) && <Badge variant="warning">{requiresAuth(route)}</Badge>}
                </div>
                <div className="truncate text-[10px] text-muted-text">{route.namespace_name} · {route.namespace}</div>
              </button>
            );
          })}
          {hasMore && (
            <div ref={sentinelRef} className="flex justify-center py-3">
              {loadingMore ? (
                <span className="text-[11px] text-muted-text">加载中…</span>
              ) : (
                <span className="text-[11px] text-muted-text">滚动加载更多</span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Right: selected route preview */}
      <div className="min-h-0 overflow-y-auto custom-scrollbar pr-1">
        {effectiveSelected ? (
          <div className="space-y-3">
            <div className="rounded-xl border border-border bg-card p-3">
              <RssRouteParamForm route={effectiveSelected} params={params} onParamsChange={setParams} />
              <div className="mt-3">
                <RssOptionsPanel options={options} onOptionsChange={setOptions} />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button variant="primary" size="sm" isLoading={feedLoading} disabled={!specReady} onClick={() => void fetchFeeds(false)}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  刷新
                </Button>
                <Button variant="outline" size="sm" isLoading={feedLoading} disabled={!specReady || feedLoading} onClick={() => void fetchFeeds(true)}>强制刷新</Button>
                {feedData?._cached && <span className="text-[11px] text-muted-text">已缓存</span>}
              </div>
            </div>

            {feedError && !feedData?.items?.length && (
              <InlineAlert title="获取失败" variant="danger" message={feedError} />
            )}
            {feedLoading && !feedData?.items?.length && <Loading label="正在获取 RSS 内容…" />}
            {!specReady && (
              <EmptyState title="请先填写必填参数" description="完成上方参数后会自动加载内容。" />
            )}
            {feedData && feedData.items && feedData.items.length > 0 && (
              <>
                <div className="flex items-center justify-between">
                  <span className="truncate text-sm font-medium text-foreground">{feedData.feed_title || '预览结果'}</span>
                  <RssDownloadMenu spec={spec} />
                </div>
                <RssFeedList items={feedData.items} feedTitle={feedData.feed_title} spec={spec} />
              </>
            )}
            {!feedLoading && feedData && !feedData.items?.length && !feedError && (
              <EmptyState title="当前路由暂无内容" description="该路由可能需要参数、Cookie，或暂无更新。" />
            )}
          </div>
        ) : (
          <EmptyState
            icon={<Star className="h-8 w-8" />}
            title="从左侧选择一个路由"
            description={loading ? '正在加载股市相关路由…' : '选中后在此预览内容。'}
          />
        )}
      </div>
    </div>
  );
};

function CategoryChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-2 py-0.5 text-[10px] transition',
        active
          ? 'border-cyan/60 bg-cyan/10 text-cyan'
          : 'border-border bg-card text-muted-text hover:text-foreground',
      )}
    >
      {label}
    </button>
  );
}

export default RssExplorePanel;
