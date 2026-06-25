import { FileText, TrendingUp } from 'lucide-react';
import { cn } from '../../utils/cn';
import { type ResearchReportResponse } from '../../api/researchReports';

const RATING_COLORS: Record<string, string> = {
  '买入': 'text-red-600 bg-red-50',
  '增持': 'text-orange-600 bg-orange-50',
  '中性': 'text-slate-500 bg-slate-50',
  '减持': 'text-green-600 bg-green-50',
  '卖出': 'text-green-700 bg-green-100',
};

export function ResearchPanel({ research }: { research: ResearchReportResponse }) {
  if (!research.items || research.items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无券商研报</p>
      </div>
    );
  }

  const ratingCount: Record<string, number> = {};
  for (const item of research.items) {
    if (item.rating) {
      ratingCount[item.rating] = (ratingCount[item.rating] || 0) + 1;
    }
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        <FileText className="h-4 w-4 text-cyan-600" />券商研报
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {research.items.length} 条 · 近 {research.days} 天
        </span>
      </h3>

      {Object.keys(ratingCount).length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">评级分布</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(ratingCount).map(([rating, count]) => (
              <span key={rating} className={cn('rounded-full px-3 py-1 text-xs font-medium', RATING_COLORS[rating] || 'bg-slate-50 text-slate-500')}>
                {rating} {count}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="divide-y divide-slate-100">
        {research.items.map((item, index) => (
          <a
            key={`${item.title}-${index}`}
            href={item.url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            className="block px-1 py-3 transition-colors hover:bg-slate-50"
          >
            <div className="flex items-start gap-2">
              {item.rating && (
                <span className={cn('mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium', RATING_COLORS[item.rating] || 'bg-slate-50 text-slate-500')}>
                  {item.rating}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <h4 className="text-sm font-medium leading-snug text-slate-800 line-clamp-2">
                  {item.title || '(无标题)'}
                </h4>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                  <span>{item.org}</span>
                  {item.industry && <span>| 行业: {item.industry}</span>}
                  {item.publish_date && <span>| {item.publish_date}</span>}
                </div>

                {item.profit_forecasts.length > 0 && (
                  <div className="mt-2 flex items-center gap-3 text-xs">
                    <TrendingUp className="h-3 w-3 text-slate-300" />
                    {item.profit_forecasts.map(fc => (
                      <span key={fc.year} className="text-slate-500">
                        {fc.year}: EPS {fc.eps.toFixed(2)}{fc.pe != null ? ` (PE ${fc.pe.toFixed(1)}x)` : ''}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
