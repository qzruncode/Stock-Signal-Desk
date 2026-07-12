import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Rss, Wand2 } from 'lucide-react';
import type { RssSubscription, FeedSpec } from '../api/rss';
import { Button, EmptyState, Loading, InlineAlert } from '../components/common';
import { cn } from '../utils/cn';
import { useRssSubscriptions } from '../hooks/useRssSubscriptions';
import { useRssNamespaces } from '../hooks/useRssNamespaces';
import { useRssFeeds } from '../hooks/useRssFeeds';
import {
  subscriptionToFeedSpec,
  upsertRssSubscription,
  deleteRssSubscription,
  reorderRssSubscriptions,
} from '../utils/rssSubscriptions';
import { SubscriptionList } from '../components/rss/SubscriptionList';
import { RssFeedList } from '../components/rss/RssFeedList';
import { RssExplorePanel } from '../components/rss/RssExplorePanel';
import { HtmlTransformerForm } from '../components/rss/HtmlTransformerForm';
import { RssDownloadMenu } from '../components/rss/RssDownloadMenu';

type Tab = 'subscriptions' | 'explore';

const RssPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('subscriptions');
  const [selectedSubId, setSelectedSubId] = useState<string | null>(null);
  const [transformerOpen, setTransformerOpen] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const didAutoSelect = useRef(false);

  const { subscriptions, loading: subsLoading, mutating: subsMutating, reload: reloadSubs } = useRssSubscriptions();
  const namespaces = useRssNamespaces();

  useEffect(() => {
    if (subsLoading || didAutoSelect.current || subscriptions.length === 0) return;
    didAutoSelect.current = true;
    setSelectedSubId(subscriptions[0].id);
  }, [subsLoading, subscriptions]);

  const selectedSub = useMemo(
    () => subscriptions.find((s) => s.id === selectedSubId) ?? null,
    [subscriptions, selectedSubId],
  );
  const subSpec: FeedSpec | null = useMemo(
    () => (selectedSub ? subscriptionToFeedSpec(selectedSub) : null),
    [selectedSub],
  );
  const { feedData, loading: feedLoading, error: feedError } = useRssFeeds(subSpec, Boolean(subSpec));

  const handleSelectSub = (sub: RssSubscription) => {
    setSelectedSubId(sub.id);
    setTab('subscriptions');
  };

  const handleDeleteSub = async (sub: RssSubscription) => {
    if (!window.confirm(`删除订阅「${sub.title}」？`)) return;
    setDeletingId(sub.id);
    try {
      await deleteRssSubscription(sub.id);
      if (selectedSubId === sub.id) {
        setSelectedSubId(subscriptions.find((item) => item.id !== sub.id)?.id ?? null);
      }
    } catch (err) {
      window.alert(`删除失败：${(err as Error).message}`);
    } finally {
      setDeletingId(null);
    }
  };

  const handleReorder = async (orderedIds: string[]) => {
    // Optimistic: useWatchlistGroups-style hook will refetch on the global event.
    try {
      await reorderRssSubscriptions(orderedIds);
    } catch (err) {
      window.alert(`排序失败：${(err as Error).message}`);
      void reloadSubs();
    }
  };

  const handleSubscribeFromExplore = async (spec: FeedSpec, title: string) => {
    try {
      const sub = await upsertRssSubscription(title, spec);
      setSelectedSubId(sub.id);
      setTab('subscriptions');
    } catch (err) {
      window.alert(`订阅失败：${(err as Error).message}`);
    }
  };

  const handleSaveHtmlSubscription = async (info: {
    title: string;
    routePath: string;
    namespace: string;
    persistParams: Record<string, string>;
  }) => {
    try {
      const sub = await upsertRssSubscription(info.title, {
        route_path: info.routePath,
        namespace: info.namespace,
        params: info.persistParams,
        options: {},
      });
      setSelectedSubId(sub.id);
      setTab('subscriptions');
    } catch (err) {
      window.alert(`订阅失败：${(err as Error).message}`);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-border bg-card/80 px-6 py-4 backdrop-blur-sm">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Rss className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-foreground">RSS 资讯</h1>
              <p className="text-xs text-muted-text">聚合股市相关 RSSHub 路由，订阅追踪市场动态</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setTransformerOpen(true)}>
              <Wand2 className="h-3.5 w-3.5" />
              网页转 RSS
            </Button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="shrink-0 border-b border-border bg-muted/30 px-6">
        <div className="flex gap-1">
          <TabButton active={tab === 'subscriptions'} onClick={() => setTab('subscriptions')}>
            我的订阅
            {subscriptions.length > 0 && (
              <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-secondary-text">
                {subscriptions.length}
              </span>
            )}
          </TabButton>
          <TabButton active={tab === 'explore'} onClick={() => setTab('explore')}>
            探索
          </TabButton>
        </div>
      </div>

      {/* Content */}
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        {tab === 'subscriptions' ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
            <div>
              <SubscriptionList
                subscriptions={subscriptions}
                loading={subsLoading}
                deletingId={deletingId}
                reordering={subsMutating}
                selectedId={selectedSubId}
                onSelect={handleSelectSub}
                onDelete={handleDeleteSub}
                onReorder={handleReorder}
              />
            </div>
            <div className="min-w-0">
              {selectedSub && (
                <div className="mb-2 flex items-center justify-between">
                  <span className="truncate text-sm font-medium text-foreground">{selectedSub.title}</span>
                  <RssDownloadMenu spec={subSpec} />
                </div>
              )}
              {feedLoading && !feedData?.items?.length && <Loading label="正在加载订阅内容…" />}
              {feedError && !feedData?.items?.length && (
                <InlineAlert title="获取失败" variant="danger" message={feedError} />
              )}
              {feedData && feedData.items && feedData.items.length > 0 && (
                <RssFeedList items={feedData.items} feedTitle={feedData.feed_title} spec={subSpec} />
              )}
              {!feedLoading && !feedError && !selectedSub && (
                <EmptyState
                  icon={<Rss className="h-8 w-8" />}
                  title="选择一个订阅"
                  description="从左侧选择订阅查看内容，或切到「探索」浏览股市相关 RSSHub 路由。"
                />
              )}
              {!feedLoading && selectedSub && feedData && !feedData.items?.length && !feedError && (
                <EmptyState title="当前订阅暂无内容" description="稍后再试或强制刷新。" />
              )}
            </div>
          </div>
        ) : (
          <div className="h-full min-h-0">
            <RssExplorePanel
              routes={namespaces.routes}
              loading={namespaces.loading}
              error={namespaces.error}
              search={namespaces.search}
              setSearch={namespaces.setSearch}
              filtered={namespaces.filtered}
              onReload={() => void namespaces.reload()}
              onSubscribe={handleSubscribeFromExplore}
            />
          </div>
        )}
      </div>

      <HtmlTransformerForm
        isOpen={transformerOpen}
        onClose={() => setTransformerOpen(false)}
        onSaveSubscription={handleSaveHtmlSubscription}
      />
    </div>
  );
};

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'border-b-2 px-3 py-2.5 text-sm font-medium transition',
        active
          ? 'border-cyan text-foreground'
          : 'border-transparent text-muted-text hover:text-foreground',
      )}
    >
      {children}
    </button>
  );
}

export default RssPage;
