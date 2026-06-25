import { FileText, Smile, Frown, FileQuestion } from 'lucide-react';
import { cn } from '../../utils/cn';
import { type SocialSentimentResponse } from '../../api/socialSentiment';

export function SocialSentimentPanel({ social }: { social: SocialSentimentResponse }) {
  const score = social.overall_score;
  const isPositive = score > 0;
  const isNegative = score < 0;
  const total = social.positive_count + social.negative_count + social.neutral_count;
  const posPct = total > 0 ? (social.positive_count / total) * 100 : 0;
  const negPct = total > 0 ? (social.negative_count / total) * 100 : 0;
  const neuPct = total > 0 ? (social.neutral_count / total) * 100 : 0;
  const maxDaily = Math.max(...social.daily_trend.map(d => d.total), 1);
  const diagnoseScore = social.diagnose_score;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        <FileText className="h-4 w-4 text-cyan-600" />社交情绪
        <span className="ml-auto text-xs font-normal text-slate-400">
          {total} 条 · 近 {social.days} 天
        </span>
      </h3>

      <div className="flex items-center gap-4 text-xs text-slate-500">
        <span>总阅读 {(social.total_read ?? 0).toLocaleString()}</span>
        <span>总评论 {(social.total_reply ?? 0).toLocaleString()}</span>
      </div>

      <div className="flex items-center gap-4">
        <div className="text-center">
          <div className={cn('text-4xl font-bold tabular-nums',
            isPositive ? 'text-red-600' : isNegative ? 'text-green-600' : 'text-slate-500')}>
            {score > 0 ? '+' : ''}{score.toFixed(0)}
          </div>
          <div className={cn('text-sm font-medium',
            isPositive ? 'text-red-600' : isNegative ? 'text-green-600' : 'text-slate-500')}>
            {isPositive ? '偏乐观' : isNegative ? '偏悲观' : '中性'}
          </div>
        </div>
        <div className="flex-1 space-y-3">
          <div className="h-4 w-full flex rounded-full overflow-hidden bg-slate-100">
            {posPct > 0 && <div className="bg-red-400 transition-all" style={{ width: `${posPct}%` }} />}
            {neuPct > 0 && <div className="bg-slate-300 transition-all" style={{ width: `${neuPct}%` }} />}
            {negPct > 0 && <div className="bg-green-400 transition-all" style={{ width: `${negPct}%` }} />}
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-red-600">乐观 {social.positive_count} ({posPct.toFixed(0)}%)</span>
            <span className="text-slate-400">中性 {social.neutral_count} ({neuPct.toFixed(0)}%)</span>
            <span className="text-green-600">悲观 {social.negative_count} ({negPct.toFixed(0)}%)</span>
          </div>
        </div>
      </div>

      {diagnoseScore != null && (
        <div className="flex items-center gap-4 text-sm">
          <span className="text-xs font-medium text-slate-400">千股千评</span>
          <span className="text-lg font-semibold text-cyan-600">{diagnoseScore.toFixed(1)}</span>
          <span className="text-xs text-slate-400">综合评分</span>
        </div>
      )}

      {social.daily_trend.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">讨论热度</p>
          <div className="flex items-end gap-1 h-16">
            {social.daily_trend.map(day => (
              <div key={day.date} className="flex-1 flex flex-col items-center justify-end h-full">
                <div className="w-full rounded-sm bg-cyan-500/70"
                  style={{ height: `${Math.max((day.total / maxDaily) * 100, 5)}%` }}
                />
                <span className="mt-1 text-[8px] text-slate-400">{day.date.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {social.score_trend.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">评分走势</p>
          <div className="flex items-end gap-1 h-16">
            {social.score_trend.map(st => {
              const h = st.score != null ? Math.max(((st.score) / 100) * 100, 5) : 0;
              return (
                <div key={st.date} className="flex-1 flex flex-col items-center justify-end h-full">
                  <div className={cn('w-full rounded-sm', st.score != null ? 'bg-amber-500/70' : 'bg-slate-200')}
                    style={{ height: `${h}%` }} />
                  <span className="mt-1 text-[8px] text-slate-400">{st.date.slice(5)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {social.items.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">逐条情绪</p>
          <div className="divide-y divide-slate-50 max-h-80 overflow-y-auto">
            {social.items.slice(0, 30).map((item, i) => {
              const Icon = item.label === 'positive' ? Smile : item.label === 'negative' ? Frown : FileQuestion;
              const color = item.label === 'positive' ? 'text-red-600' : item.label === 'negative' ? 'text-green-600' : 'text-slate-400';
              return (
                <a key={i} href={item.url || '#'} target="_blank" rel="noopener noreferrer"
                  className="flex items-start gap-2 px-1 py-2 text-xs hover:bg-slate-50">
                  <Icon className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', color)} />
                  <div className="min-w-0 flex-1">
                    <span className={cn('font-medium',
                      item.label === 'positive' ? 'text-red-700' : item.label === 'negative' ? 'text-green-700' : 'text-slate-500')}>
                      {item.title || '(无标题)'}
                    </span>
                    <span className="ml-2 text-slate-400">[{item.source}]</span>
                  </div>
                  <span className={cn('tabular-nums shrink-0', color)}>
                    {item.sentiment_score > 0 ? '+' : ''}{item.sentiment_score.toFixed(2)}
                  </span>
                </a>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
