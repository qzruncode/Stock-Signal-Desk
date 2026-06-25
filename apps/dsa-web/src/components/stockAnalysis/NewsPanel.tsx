import { Newspaper } from 'lucide-react';
import { cn } from '../../utils/cn';
import { type NewsResponse } from '../../api/news';

const CATEGORY_COLORS: Record<string, string> = {
  '新闻': 'bg-blue-50 text-blue-600',
  '研报': 'bg-amber-50 text-amber-600',
};

export function NewsPanel({ news }: { news: NewsResponse }) {
  if (!news.items || news.items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无相关新闻</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Newspaper className="h-4 w-4 text-cyan-600" />相关新闻
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {news.items.length} 条 · 近 {news.days} 天
        </span>
      </h3>
      <div className="divide-y divide-slate-100">
        {news.items.map((item, index) => (
          <a
            key={`${item.title}-${index}`}
            href={item.url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            className="block px-1 py-3 transition-colors hover:bg-slate-50"
          >
            <div className="flex items-start gap-2">
              {item.category && (
                <span className={cn(
                  'mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium',
                  CATEGORY_COLORS[item.category] || 'bg-slate-50 text-slate-500',
                )}>
                  {item.category}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <h4 className="text-sm font-medium leading-snug text-slate-800 line-clamp-2">
                  {item.title || '(无标题)'}
                </h4>
                {item.summary && (
                  <p className="mt-1 text-xs leading-relaxed text-slate-500 line-clamp-2">
                    {item.summary}
                  </p>
                )}
              </div>
            </div>
            <div className="mt-1.5 flex items-center gap-3 text-xs text-slate-400">
              <span>{item.source || '-'}</span>
              {item.publish_time && (
                <>
                  <span className="text-slate-300">|</span>
                  <span>{new Date(item.publish_time).toLocaleString('zh-CN')}</span>
                </>
              )}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
