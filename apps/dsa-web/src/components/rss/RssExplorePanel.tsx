import React, { useMemo, useState } from 'react';
import { RefreshCw, Star } from 'lucide-react';
import type { RssRouteDescriptor, RssFeedOptions, FeedSpec } from '../../api/rss';
import { Button, EmptyState, InlineAlert, Loading, Badge } from '../common';
import { cn } from '../../utils/cn';
import { useRssFeeds } from '../../hooks/useRssFeeds';
import { RssRouteParamForm } from './RssRouteParamForm';
import { RssOptionsPanel } from './RssOptionsPanel';
import { RssFeedList } from './RssFeedList';
import { RssDownloadMenu } from './RssDownloadMenu';
import { paramsFromExample, parseRouteParams, requiresAuth } from '../../utils/rssRoute';

const EMPTY_OPTIONS: RssFeedOptions = {};

export interface RssExplorePanelProps {
  loading: boolean;
  error: string | null;
  filtered: RssRouteDescriptor[];
  /** Whether the route picker is expanded. The settings page keeps it collapsed by default. */
  sourceListOpen?: boolean;
}

export const RssExplorePanel: React.FC<RssExplorePanelProps> = ({
  loading, error, filtered, sourceListOpen = true,
}) => {
  const [selected, setSelected] = useState<RssRouteDescriptor | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [options, setOptions] = useState<RssFeedOptions>(EMPTY_OPTIONS);

  // Effective selection: once the user picks a route it stays selected even when
  // a search filter hides it from the list — silently swapping the right pane
  // to filtered[0] would be confusing ("I didn't click another route"). Before
  // any pick, default to the first visible route so the pane isn't empty on
  // first load. Derived (not an effect) to avoid setState-in-effect.
  const effectiveSelected: RssRouteDescriptor | null = selected ?? filtered[0] ?? null;

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

  return (
    <div className={cn(
      'grid min-h-0 grid-cols-1 gap-3',
      !sourceListOpen && 'lg:h-full',
    )}>
      {/* Adaptive route-card grid. */}
      {sourceListOpen && <div id="rss-source-list" className="max-h-[30rem] min-h-0 overflow-y-auto rounded-xl border border-border bg-card p-2 custom-scrollbar">
          {loading && <div className="p-4"><Loading label="加载路由…" /></div>}
          {!loading && error && <div className="p-3 text-xs text-danger">{error}</div>}
          {!loading && !error && filtered.length === 0 && (
            <div className="p-4 text-center text-xs text-muted-text">未找到匹配路由</div>
          )}
          {!loading && !error && filtered.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-2">
            {filtered.map((route) => {
            const active = effectiveSelected?.route_path === route.route_path;
            return (
              <button
                key={route.route_path}
                type="button"
                onClick={() => handleSelect(route)}
                className={cn(
                  'block min-w-0 rounded-lg border p-3 text-left transition',
                  active ? 'border-cyan/60 bg-cyan/5' : 'border-border/70 hover:border-cyan/30 hover:bg-muted/60',
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
            </div>
          )}
      </div>}

      {/* Selected route preview */}
      <div className={cn('min-h-0 custom-scrollbar', !sourceListOpen && 'lg:overflow-y-auto lg:pr-1')}>
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

export default RssExplorePanel;
