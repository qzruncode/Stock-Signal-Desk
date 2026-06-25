import { AlertTriangle } from 'lucide-react';
import { cn } from '../../utils/cn';
import { type RiskEventsResponse } from '../../api/riskEvents';

export function RiskEventsPanel({ riskEvents }: { riskEvents: RiskEventsResponse }) {
  if (!riskEvents.items || riskEvents.items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">近阶段未扫描到明确风险线索</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <AlertTriangle className="h-4 w-4 text-cyan-600" />风险线索
        </h3>
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {riskEvents.analysis.total_events} 条 · 近 {riskEvents.days} 天
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-5">
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-slate-400">线索总数</span>
          <span className="text-sm font-semibold text-slate-900">{riskEvents.analysis.total_events}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-slate-400">高风险标签</span>
          <span className="text-sm text-red-600">{riskEvents.analysis.severity_distribution.high}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-slate-400">中风险标签</span>
          <span className="text-sm text-orange-600">{riskEvents.analysis.severity_distribution.medium}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-slate-400">低风险标签</span>
          <span className="text-sm text-slate-600">{riskEvents.analysis.severity_distribution.low}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-slate-400">公司公告</span>
          <span className="text-sm text-slate-900">{riskEvents.analysis.source_distribution?.announcement ?? 0}</span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-slate-400">相关新闻</span>
          <span className="text-sm text-slate-900">{riskEvents.analysis.source_distribution?.news ?? 0}</span>
        </div>
      </div>

      {riskEvents.analysis.top_risk_labels.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">高频线索主题</p>
          <div className="flex flex-wrap gap-2">
            {riskEvents.analysis.top_risk_labels.map((item) => (
              <span key={item} className="rounded-full bg-red-50 px-2.5 py-1 text-xs text-red-700">
                {item}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="divide-y divide-slate-100">
        {riskEvents.items.map((item, index) => (
          <a
            key={`${item.title}-${index}`}
            href={item.url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            className="block px-1 py-3 transition-colors hover:bg-slate-50"
          >
            <div className="flex items-start gap-2">
              <span className={cn(
                'mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium',
                item.severity === 'high'
                  ? 'bg-red-50 text-red-600'
                  : item.severity === 'medium'
                    ? 'bg-orange-50 text-orange-600'
                    : 'bg-slate-50 text-slate-500',
              )}>
                {item.risk_label}
              </span>
              <div className="min-w-0 flex-1">
                <h4 className="text-sm font-medium leading-snug text-slate-800 line-clamp-2">
                  {item.title || '(无标题)'}
                </h4>
                {item.risk_summary && (
                  <p className="mt-1 text-xs leading-relaxed text-slate-500 line-clamp-3">
                    {item.risk_summary}
                  </p>
                )}
                {item.tags.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {item.tags.map((tag) => (
                      <span key={tag} className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="mt-1.5 flex items-center gap-3 text-xs text-slate-400">
              <span>{item.source || '-'}</span>
              <span className="text-slate-300">|</span>
              <span>{item.source_type === 'announcement' ? '公告' : '新闻'}</span>
              {item.date && (
                <>
                  <span className="text-slate-300">|</span>
                  <span>{item.date}</span>
                </>
              )}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
