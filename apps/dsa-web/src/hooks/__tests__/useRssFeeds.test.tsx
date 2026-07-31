import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FeedSpec, RssFeedBySpecResponse } from '../../api/rss';

// Mock rssApi.getFeedsBySpec with controllable deferreds so we can simulate a
// slow request being aborted by a faster one — the exact race that previously
// dropped the loading flag mid-flight.
vi.mock('../../api/rss', async () => {
  const actual = await vi.importActual<typeof import('../../api/rss')>('../../api/rss');
  return {
    ...actual,
    rssApi: {
      ...actual.rssApi,
      getFeedsBySpec: vi.fn(),
    },
  };
});

const { rssApi } = await import('../../api/rss');
const { useRssFeeds } = await import('../useRssFeeds');

const SPEC_A: FeedSpec = { route_path: '/followin/news/:lang?', namespace: 'followin', params: {}, options: {} };
const SPEC_B: FeedSpec = { route_path: '/xueqiu/timeline', namespace: 'xueqiu', params: {}, options: {} };

const feed = (title: string): RssFeedBySpecResponse => ({
  route_path: title,
  params: {},
  options: {},
  feed_title: title,
  feed_link: '',
  items: [],
  errors: [],
  _fetched_at: '',
  _cached: false,
});

/** A deferred promise the test resolves/rejects manually, mirroring axios'
 *  behavior of rejecting with an AbortError when its signal aborts. */
function makeDeferred() {
  let resolve!: (v: RssFeedBySpecResponse) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<RssFeedBySpecResponse>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('useRssFeeds', () => {
  beforeEach(() => {
    vi.mocked(rssApi.getFeedsBySpec).mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps loading true while the active (latest) request is in flight, even after an earlier slow request is aborted', async () => {
    // Slow request A (uncached /followin/news on first hit) + fast request B.
    const slow = makeDeferred();
    const fast = makeDeferred();

    let call = 0;
    vi.mocked(rssApi.getFeedsBySpec).mockImplementation((_spec, signal) => {
      call += 1;
      const deferred = call === 1 ? slow : fast;
      // When aborted, reject with a DOMException AbortError — exactly what
      // axios does, and what the hook must treat as "not my request anymore".
      signal?.addEventListener('abort', () => {
        deferred.reject(new DOMException('aborted', 'AbortError'));
      });
      return deferred.promise;
    });

    const { result, rerender } = renderHook(({ spec }) => useRssFeeds(spec, Boolean(spec)), {
      initialProps: { spec: SPEC_A as FeedSpec | null },
    });

    // Request A is in flight → loading true.
    await waitFor(() => expect(result.current.loading).toBe(true));

    // User switches route → spec B. This aborts A and starts B.
    rerender({ spec: SPEC_B });

    // Let the aborted request A's rejection (AbortError) flush. Before the fix,
    // its finally() would run setLoading(false) AFTER B's setLoading(true),
    // wiping the loading flag while B is still pending.
    await act(async () => {
      await Promise.resolve();
    });

    // The active request B is still pending → loading MUST stay true.
    expect(result.current.loading).toBe(true);

    // B resolves → loading clears and its data surfaces (not A's stale data).
    await act(async () => {
      fast.resolve(feed('B'));
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.feedData?.feed_title).toBe('B');

    // slow.resolve is never consumed (aborted); leaving it pending is fine.
    slow.resolve(feed('A-should-be-ignored'));
  });

  it('clears loading when the active request errors', async () => {
    vi.mocked(rssApi.getFeedsBySpec).mockRejectedValueOnce(new Error('boom'));

    const { result } = renderHook(({ spec }) => useRssFeeds(spec, Boolean(spec)), {
      initialProps: { spec: SPEC_A as FeedSpec | null },
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('boom');
    expect(result.current.feedData).toBeNull();
  });
});
