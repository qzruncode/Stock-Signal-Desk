import React from 'react';
import { AlertTriangle, CheckCircle2, FileQuestion, Smile, Frown } from 'lucide-react';
import { cn } from '../../utils/cn';
import { type SentimentResponse } from '../../api/sentiment';

const LABEL_CONFIG = {
  positive: { icon: Smile, label: '乐观', color: 'text-red-600', bg: 'bg-red-50', barBg: 'bg-red-500' },
  negative: { icon: Frown, label: '悲观', color: 'text-green-600', bg: 'bg-green-50', barBg: 'bg-green-500' },
  neutral: { icon: FileQuestion, label: '中性', color: 'text-slate-500', bg: 'bg-slate-50', barBg: 'bg-slate-400' },
};

export function SentimentPanel({ sentiment }: { sentiment: SentimentResponse }) {
  const score = sentiment.sentiment_score;
  const isPositive = score > 0;
  const isNegative = score < 0;
  const total = sentiment.positive_count + sentiment.negative_count + sentiment.neutral_count;

  const sentimentIcon = isPositive ? CheckCircle2 : isNegative ? AlertTriangle : FileQuestion;
  const sentimentColor = isPositive ? 'text-red-600' : isNegative ? 'text-green-600' : 'text-slate-500';
  const sentimentLabel = isPositive ? '偏乐观' : isNegative ? '偏悲观' : '中性';

  const posPct = total > 0 ? (sentiment.positive_count / total) * 100 : 0;
  const negPct = total > 0 ? (sentiment.negative_count / total) * 100 : 0;
  const neuPct = total > 0 ? (sentiment.neutral_count / total) * 100 : 0;

  const maxDaily = Math.max(...sentiment.daily_trend.map(d => d.total), 1);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        {React.createElement(sentimentIcon, { className: cn('h-5 w-5', sentimentColor) })}
        舆情情绪
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {total} 条 · 近 {sentiment.days} 天
        </span>
      </h3>

      <div className="flex items-center gap-4">
        <div className="text-center">
          <div className={cn('text-4xl font-bold tabular-nums', sentimentColor)}>
            {score > 0 ? '+' : ''}{score.toFixed(0)}
          </div>
          <div className={cn('text-sm font-medium', sentimentColor)}>舆情分数</div>
        </div>
        <div className="flex-1 space-y-3">
          <div className="h-4 w-full flex rounded-full overflow-hidden bg-slate-100">
            {posPct > 0 && <div className={cn('bg-red-400 transition-all')} style={{ width: `${posPct}%` }} />}
            {neuPct > 0 && <div className={cn('bg-slate-300 transition-all')} style={{ width: `${neuPct}%` }} />}
            {negPct > 0 && <div className={cn('bg-green-400 transition-all')} style={{ width: `${negPct}%` }} />}
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-red-600">乐观 {sentiment.positive_count} ({posPct.toFixed(0)}%)</span>
            <span className="text-slate-400">中性 {sentiment.neutral_count} ({neuPct.toFixed(0)}%)</span>
            <span className="text-green-600">悲观 {sentiment.negative_count} ({negPct.toFixed(0)}%)</span>
          </div>
        </div>
      </div>

      <div className={cn('rounded-xl border p-3 text-center text-sm font-medium',
        isPositive ? 'border-red-100 bg-red-50 text-red-700' :
        isNegative ? 'border-green-100 bg-green-50 text-green-700' :
        'border-slate-100 bg-slate-50 text-slate-600',
      )}>
        当前市场情绪: {sentimentLabel}
      </div>

      {sentiment.daily_trend.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">讨论热度趋势</p>
          <div className="flex items-end gap-1 h-16">
            {sentiment.daily_trend.map((day) => (
              <div
                key={day.date}
                className="flex-1 flex flex-col items-center justify-end h-full"
                aria-label={`${day.date}: ${day.total} 条`}
              >
                <div
                  className="w-full rounded-sm min-h-0.5 bg-cyan-500/70"
                  style={{ height: `${Math.max((day.total / maxDaily) * 100, 5)}%` }}
                />
                <span className="mt-1 text-[8px] text-slate-400">
                  {day.date.slice(5)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {sentiment.top_keywords.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">高频关键词</p>
          <div className="flex flex-wrap gap-1.5">
            {sentiment.top_keywords.slice(0, 15).map((kw) => (
              <span
                key={kw}
                className="rounded-full bg-cyan-50 px-2.5 py-0.5 text-xs text-cyan-700"
              >
                {kw}
              </span>
            ))}
          </div>
        </div>
      )}

      {sentiment.items.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">逐条情绪分析</p>
          <div className="divide-y divide-slate-50 max-h-80 overflow-y-auto">
            {sentiment.items.slice(0, 30).map((item, index) => {
              const cfg = LABEL_CONFIG[item.label];
              const Icon = cfg.icon;
              return (
                <div key={index} className="flex items-start gap-2 px-1 py-2 text-xs">
                  <Icon className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', cfg.color)} />
                  <div className="min-w-0 flex-1">
                    <span className={cn('font-medium', item.label === 'positive' ? 'text-red-700' : item.label === 'negative' ? 'text-green-700' : 'text-slate-500')}>
                      {item.title || '(无标题)'}
                    </span>
                    <span className="ml-2 text-slate-400">[{item.source}]</span>
                  </div>
                  <span className={cn('tabular-nums shrink-0', cfg.color)}>
                    {item.sentiment_score > 0 ? '+' : ''}{item.sentiment_score.toFixed(2)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
