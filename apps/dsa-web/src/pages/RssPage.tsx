import React, { useEffect, useMemo, useState } from 'react';
import { ExternalLink, RefreshCw, Rss, Clock } from 'lucide-react';
import { rssApi, type RssFeedResponse, type RssSourceOption } from '../api/rss';
import { cn } from '../utils/cn';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useRssFeeds } from '../hooks/useRssFeeds';

// ── 分组定义 ──────────────────────────────────────────────

const GROUP_ORDER = ['快讯', '宏观', '个股', '研报', '交易所/监管', '监管/公告', '海外', '科技创投'];

// ── 子组件 ──────────────────────────────────────────────

function SourceSelector({
  sources,
  value,
  onChange,
}: {
  sources: RssSourceOption[];
  value: string;
  onChange: (id: string) => void;
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, RssSourceOption[]>();
    for (const s of sources) {
      const g = s.group || '其他';
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(s);
    }
    return GROUP_ORDER
      .filter((g) => map.has(g))
      .map((g) => ({ group: g, items: map.get(g)! }));
  }, [sources]);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm transition hover:border-cyan-300 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
    >
      {grouped.map(({ group, items }) => (
        <optgroup key={group} label={group}>
          {items.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

function FeedItemCard({ item }: { item: RssFeedResponse['items'][0] }) {
  const [expanded, setExpanded] = useState(false);
  const summaryLimit = 120;
  const needsTruncate = item.summary.length > summaryLimit;
  const displaySummary = expanded
    ? item.summary
    : item.summary.slice(0, summaryLimit);

  return (
    <div className="group rounded-xl border border-slate-200 bg-white p-4 transition hover:border-cyan-200 hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold leading-snug text-slate-800">
            {item.link ? (
              <a
                href={item.link}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-cyan-700 hover:underline"
              >
                {item.title || '无标题'}
              </a>
            ) : (
              item.title || '无标题'
            )}
          </h3>
          {item.summary && (
            <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
              {displaySummary}
              {needsTruncate && !expanded && (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className="ml-1 text-cyan-600 hover:underline"
                >
                  展开
                </button>
              )}
              {needsTruncate && expanded && (
                <button
                  type="button"
                  onClick={() => setExpanded(false)}
                  className="ml-1 text-cyan-600 hover:underline"
                >
                  收起
                </button>
              )}
            </p>
          )}
        </div>
        {item.link && (
          <a
            href={item.link}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-slate-300 transition hover:text-cyan-500"
            aria-label="打开原文"
          >
            <ExternalLink className="h-4 w-4" />
          </a>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
        {item.published && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {formatTime(item.published)}
          </span>
        )}
        {item.author && <span>{item.author}</span>}
        {item.tags.slice(0, 3).map((tag) => (
          <span
            key={tag}
            className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500"
          >
            {tag}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── 工具 ──────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 60) return `${diffMin} 分钟前`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour} 小时前`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 30) return `${diffDay} 天前`;
    return d.toLocaleDateString('zh-CN');
  } catch {
    return iso;
  }
}

// ── 页面 ──────────────────────────────────────────────

const RssPage: React.FC = () => {
  const [sources, setSources] = useState<RssSourceOption[]>([]);
  const [source, setSource] = useState('wallstreetcn');
  const [stockInput, setStockInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [uid, setUid] = useState('');
  const [subType, setSubType] = useState('');
  const [category, setCategory] = useState('');

  // 输入防抖
  const debouncedStockCode = useDebouncedValue(stockInput.trim(), 500);

  // 加载源列表
  useEffect(() => {
    rssApi.getSources().then((res) => {
      setSources(res.sources);
    }).catch(() => {
      // 静默失败，前端仍可手动输入 source
    });
  }, []);

  // 当前源配置
  const currentSource = useMemo(
    () => sources.find((s) => s.id === source),
    [sources, source],
  );

  // 切换源时重置条件参数并设置默认值
  useEffect(() => {
    if (!currentSource) return;
    (() => {
      setStockInput('');
      setKeyword('');
      setUid('');
      if (currentSource.default_type) setSubType(currentSource.default_type);
      else setSubType('');
      if (currentSource.default_category) setCategory(currentSource.default_category);
      else setCategory('');
    })();
  }, [currentSource]);

  // 获取数据
  const { feedData, loading, error, fetchFeeds } = useRssFeeds({
    source,
    stockCode: debouncedStockCode,
    keyword,
    uid,
    subType,
    category,
    currentSource,
    sourcesLength: sources.length,
  });

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 头部 */}
      <div className="shrink-0 border-b border-slate-100 bg-white/80 px-6 py-5 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-orange-50 text-orange-500">
            <Rss className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-slate-900">RSS 资讯</h1>
            <p className="text-xs text-slate-400">聚合财经 RSS 源，追踪市场动态</p>
          </div>
        </div>
      </div>

      {/* 工具栏 */}
      <div className="shrink-0 border-b border-slate-100 bg-slate-50/50 px-6 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <SourceSelector sources={sources} value={source} onChange={setSource} />

          {currentSource?.requires_stock && (
            <input
              type="text"
              value={stockInput}
              onChange={(e) => setStockInput(e.target.value)}
              placeholder={currentSource?.stock_placeholder || '股票代码 (如 600519)'}
              className="h-10 w-36 rounded-lg border border-slate-200 bg-white px-3 text-sm shadow-sm transition placeholder:text-slate-300 hover:border-cyan-300 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            />
          )}

          {currentSource?.requires_keyword && (
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value.trim())}
              placeholder={currentSource.keyword_placeholder || '关键词'}
              className="h-10 w-52 rounded-lg border border-slate-200 bg-white px-3 text-sm shadow-sm transition placeholder:text-slate-300 hover:border-cyan-300 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            />
          )}

          {currentSource?.requires_uid && (
            <input
              type="text"
              value={uid}
              onChange={(e) => setUid(e.target.value.trim())}
              placeholder={currentSource.uid_placeholder || '用户 UID'}
              className="h-10 w-40 rounded-lg border border-slate-200 bg-white px-3 text-sm shadow-sm transition placeholder:text-slate-300 hover:border-cyan-300 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            />
          )}

          {currentSource?.requires_type && currentSource.type_options.length > 0 && (
            <select
              value={subType}
              onChange={(e) => setSubType(e.target.value)}
              className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm shadow-sm transition hover:border-cyan-300 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            >
              {currentSource.type_options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          )}

          {currentSource?.requires_category && currentSource.category_options.length > 0 && (
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm shadow-sm transition hover:border-cyan-300 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            >
              {currentSource.category_options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          )}

          <button
            type="button"
            onClick={() => void fetchFeeds()}
            disabled={loading}
            className={cn(
              'flex h-10 items-center gap-2 rounded-lg px-4 text-sm font-medium shadow-sm transition',
              loading
                ? 'cursor-not-allowed bg-slate-100 text-slate-400'
                : 'bg-cyan-600 text-white hover:bg-cyan-700',
            )}
          >
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            刷新
          </button>

          {feedData?._cached && (
            <span className="text-[11px] text-slate-400">已缓存</span>
          )}
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {/* 错误提示 */}
        {error && !feedData?.items?.length && (
          <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-6 text-center">
            <p className="text-sm font-medium text-red-600">{error}</p>
            <p className="mt-1 text-xs text-red-400">
              请检查网络连接或 RSSHub 实例是否可用
            </p>
          </div>
        )}

        {/* 加载中 */}
        {loading && !feedData?.items?.length && (
          <div className="flex h-40 items-center justify-center">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          </div>
        )}

        {/* 空状态 */}
        {!loading && feedData && !feedData.items?.length && (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center">
            <Rss className="mx-auto h-8 w-8 text-slate-300" />
            <p className="mt-3 text-sm text-slate-400">
              当前源暂无内容，请切换源或调整筛选条件
            </p>
          </div>
        )}

        {/* Feed 列表 */}
        {feedData && feedData.items && feedData.items.length > 0 && (
          <div className="space-y-3">
            {feedData.feed_title && (
              <div className="mb-1 flex items-center gap-2 text-xs text-slate-400">
                <Rss className="h-3.5 w-3.5" />
                <span>{feedData.feed_title}</span>
                <span>·</span>
                <span>{feedData.items.length} 条</span>
              </div>
            )}
            {feedData.items.map((item, idx) => (
              <FeedItemCard key={`${item.link}-${idx}`} item={item} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default RssPage;
