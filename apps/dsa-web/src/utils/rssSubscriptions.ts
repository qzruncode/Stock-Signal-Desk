import { rssApi, type RssSubscription, type FeedSpec } from '../api/rss';

export const RSS_SUBSCRIPTIONS_UPDATED_EVENT = 'dsa-rss-subscriptions-updated';

function notifySubscriptionsUpdated() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(RSS_SUBSCRIPTIONS_UPDATED_EVENT));
}

export async function fetchRssSubscriptions(): Promise<RssSubscription[]> {
  const res = await rssApi.listSubscriptions();
  return res.subscriptions || [];
}

/** Create a subscription from a title + FeedSpec, or replace an existing one when the title matches. */
export async function upsertRssSubscription(
  title: string,
  spec: FeedSpec,
): Promise<RssSubscription> {
  const sub = await rssApi.upsertSubscription({
    title,
    namespace: spec.namespace,
    route_path: spec.route_path,
    params: spec.params,
    options: spec.options,
  });
  notifySubscriptionsUpdated();
  return sub;
}

export async function updateRssSubscription(
  id: string,
  patch: {
    title?: string;
    namespace?: string;
    route_path?: string;
    params?: Record<string, string>;
    options?: FeedSpec['options'];
  },
): Promise<RssSubscription> {
  const sub = await rssApi.updateSubscription(id, patch);
  notifySubscriptionsUpdated();
  return sub;
}

export async function deleteRssSubscription(id: string): Promise<void> {
  await rssApi.deleteSubscription(id);
  notifySubscriptionsUpdated();
}

export async function reorderRssSubscriptions(orderedIds: string[]): Promise<void> {
  await rssApi.reorderSubscriptions(orderedIds);
  notifySubscriptionsUpdated();
}

/** Convert a stored subscription into a FeedSpec for the shared fetch path. */
export function subscriptionToFeedSpec(sub: RssSubscription): FeedSpec {
  return {
    route_path: sub.routePath,
    namespace: sub.namespace,
    params: sub.params,
    options: sub.options,
  };
}
