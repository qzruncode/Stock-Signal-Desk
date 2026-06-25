import React from 'react';
import { AlertTriangle, CheckCircle2, FileQuestion, Megaphone, Newspaper, Smile, Frown, FileText, TrendingUp } from 'lucide-react';
import { cn } from '../utils/cn';
import { type NewsResponse } from '../api/news';
import { type AnnouncementsResponse } from '../api/announcements';
import { type SentimentResponse } from '../api/sentiment';
import { type ResearchReportResponse } from '../api/researchReports';
import { type SocialSentimentResponse } from '../api/socialSentiment';
import { type RiskEventsResponse } from '../api/riskEvents';

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Sentiment panel
// ---------------------------------------------------------------------------

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

  // Sentiment emoji color
  const sentimentIcon = isPositive ? CheckCircle2 : isNegative ? AlertTriangle : FileQuestion;
  const sentimentColor = isPositive ? 'text-red-600' : isNegative ? 'text-green-600' : 'text-slate-500';
  const sentimentLabel = isPositive ? '偏乐观' : isNegative ? '偏悲观' : '中性';

  // Bar widths
  const posPct = total > 0 ? (sentiment.positive_count / total) * 100 : 0;
  const negPct = total > 0 ? (sentiment.negative_count / total) * 100 : 0;
  const neuPct = total > 0 ? (sentiment.neutral_count / total) * 100 : 0;

  // Max daily for trend chart
  const maxDaily = Math.max(...sentiment.daily_trend.map(d => d.total), 1);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      {/* 情绪概览 */}
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        {React.createElement(sentimentIcon, { className: cn('h-5 w-5', sentimentColor) })}
        舆情情绪
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {total} 条 · 近 {sentiment.days} 天
        </span>
      </h3>

      {/* 舆情分数 */}
      <div className="flex items-center gap-4">
        <div className="text-center">
          <div className={cn('text-4xl font-bold tabular-nums', sentimentColor)}>
            {score > 0 ? '+' : ''}{score.toFixed(0)}
          </div>
          <div className={cn('text-sm font-medium', sentimentColor)}>舆情分数</div>
        </div>
        <div className="flex-1 space-y-3">
          {/* 情绪分布条形图 */}
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

      {/* 情绪倾向标注 */}
      <div className={cn('rounded-xl border p-3 text-center text-sm font-medium',
        isPositive ? 'border-red-100 bg-red-50 text-red-700' :
        isNegative ? 'border-green-100 bg-green-50 text-green-700' :
        'border-slate-100 bg-slate-50 text-slate-600',
      )}>
        当前市场情绪: {sentimentLabel}
      </div>

      {/* 讨论热度趋势 */}
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

      {/* 关键词 */}
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

      {/* 逐条情绪 */}
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


// ---------------------------------------------------------------------------
// News panel
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Announcements panel
// ---------------------------------------------------------------------------

export function AnnouncementsPanel({ announcements }: { announcements: AnnouncementsResponse }) {
  if (!announcements.items || announcements.items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无公司公告</p>
      </div>
    );
  }

  const noticeTypeEntries = Object.entries(announcements.analysis?.notice_type_distribution ?? {}).slice(0, 8);
  const keyEvents = announcements.analysis?.key_events ?? [];

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Megaphone className="h-4 w-4 text-cyan-600" />公司公告
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {announcements.items.length} 条 · 近 {announcements.days} 天
        </span>
      </h3>

      {noticeTypeEntries.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">公告分类分布</p>
          <div className="flex flex-wrap gap-2">
            {noticeTypeEntries.map(([label, count]) => (
              <span
                key={label}
                className="rounded-full border border-purple-100 bg-purple-50 px-2.5 py-1 text-xs text-purple-700"
              >
                {label} {count}
              </span>
            ))}
          </div>
        </div>
      )}

      {keyEvents.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">重点公告</p>
          <div className="space-y-2">
            {keyEvents.slice(0, 3).map((item, index) => (
              <div
                key={`${item.title}-${index}`}
                className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2"
              >
                <div className="flex items-start gap-2">
                  <span className={cn(
                    'mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium',
                    item.importance === 'high'
                      ? 'bg-red-50 text-red-600'
                      : 'bg-orange-50 text-orange-600',
                  )}>
                    {item.importance === 'high' ? '高重要' : '中重要'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-800 line-clamp-2">{item.title || '(无标题)'}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {item.event_type || 'general'}
                      {item.date ? ` · ${item.date}` : ''}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="divide-y divide-slate-100">
        {announcements.items.map((item, index) => (
          <a
            key={`${item.title}-${index}`}
            href={item.url || '#'}
            target="_blank"
            rel="noopener noreferrer"
            className="block px-1 py-3 transition-colors hover:bg-slate-50"
          >
            <div className="flex items-start gap-2">
              {item.notice_type && (
                <span className="mt-0.5 shrink-0 rounded bg-purple-50 px-1.5 py-0.5 text-[10px] font-medium text-purple-600">
                  {item.notice_type}
                </span>
              )}
              <h4 className="text-sm font-medium leading-snug text-slate-800 line-clamp-2">
                {item.title || '(无标题)'}
              </h4>
            </div>
            <div className="mt-1.5 text-xs text-slate-400">
              {item.publish_date || '-'}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

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

// ---------------------------------------------------------------------------
// Research Report panel
// ---------------------------------------------------------------------------

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

  // Rating distribution
  const ratingCount: Record<string, number> = {};
  for (const item of research.items) {
    if (item.rating) {
      ratingCount[item.rating] = (ratingCount[item.rating] || 0) + 1;
    }
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm space-y-5">
      {/* Header */}
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
        <FileText className="h-4 w-4 text-cyan-600" />券商研报
        <span className="ml-auto text-xs font-normal text-slate-400">
          共 {research.items.length} 条 · 近 {research.days} 天
        </span>
      </h3>

      {/* Rating summary */}
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

      {/* Report list */}
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

                {/* Profit forecasts */}
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

// ---------------------------------------------------------------------------
// Social Sentiment panel
// ---------------------------------------------------------------------------

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

      {/* 热度概览 */}
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
