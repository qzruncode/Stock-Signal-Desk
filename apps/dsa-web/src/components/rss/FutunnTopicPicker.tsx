import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { rssApi, type FutunnTopic } from '../../api/rss';
import { Select } from '../common';

export interface FutunnTopicPickerProps {
  /** Current topic id (the value actually submitted for the route param). */
  value: string;
  /** Called when the user picks a topic. Receives the topicId (idx) as a string. */
  onSelect: (id: string) => void;
}

/**
 * Picker for the ``/futunn/topic/:id`` route's required ``id`` param.
 *
 * RSSHub metadata only says "Topic ID, can be found in URL" — no option list.
 * futunn exposes its topic list via ``news-site-api/main/get-topics-list``
 * (paginated, no auth), which the route handler itself uses to look up a topic's
 * title/description. Our backend proxies that list (paginated accumulation +
 * 6h cache), so the user can pick a topic by name instead of hunting for the
 * numeric idx in a futunn URL.
 *
 * The list only covers topics the upstream API returns (hot-first by subscribed
 * count) — a manual-entry fallback covers any topic not in the list. Unlike
 * ``/cls/subject/:id?`` the ``id`` here is **required** (path has no ``?``), so
 * there is no "默认" empty option: the user must pick or type one.
 */
export const FutunnTopicPicker: React.FC<FutunnTopicPickerProps> = ({
  value,
  onSelect,
}) => {
  const [topics, setTopics] = useState<FutunnTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);

  useEffect(() => {
    let active = true;
    void rssApi
      .getFutunnTopics({})
      .then((res) => {
        if (!active) return;
        setTopics(res.topics || []);
        if (res._error && !(res.topics || []).length) setError(res._error);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError((err as Error)?.message || '话题列表加载失败');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm text-muted-text">
        <Loader2 className="h-4 w-4 animate-spin text-cyan" />
        正在加载话题列表…
      </div>
    );
  }

  // Manual-entry fallback: remote list unavailable, or the user wants a topic
  // outside the proxied list.
  if (manual || (error && topics.length === 0)) {
    return (
      <div className="flex flex-col">
        <label className="mb-2 text-sm font-medium text-foreground">id（手动输入）</label>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={value}
            onChange={(e) => onSelect(e.target.value)}
            placeholder="输入话题编号，如 1267"
            className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 py-2.5 text-sm text-foreground transition-all duration-200 focus:outline-none"
          />
          {topics.length > 0 && (
            <button
              type="button"
              onClick={() => setManual(false)}
              className="shrink-0 text-[11px] text-cyan hover:underline"
            >
              改为选择
            </button>
          )}
        </div>
        {error && <p className="mt-1 text-[11px] text-danger">{error}（可手动输入 id）</p>}
      </div>
    );
  }

  const options = topics.map((t) => ({
    value: String(t.topicId),
    label: `${t.title}（${t.subscribed.toLocaleString()} 关注 · #${t.topicId}）`,
  }));

  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between">
        <label className="text-sm font-medium text-foreground">id（话题）</label>
        <button type="button" onClick={() => setManual(true)} className="text-[11px] text-cyan hover:underline">
          手动输入 id
        </button>
      </div>
      <Select
        value={value}
        onChange={onSelect}
        options={options}
        placeholder={topics.length ? '选择话题' : '无可用话题，可手动输入 id'}
      />
      {topics.length === 0 && !error && (
        <p className="mt-1 text-[11px] text-muted-text">未获取到话题列表，可点「手动输入 id」。</p>
      )}
      <p className="mt-1 text-[11px] text-muted-text">
        仅列出热门话题（由富途话题列表代理）；完整编号见 news.futunn.com/news-topics 页面 URL。
      </p>
    </div>
  );
};
