import { Megaphone } from 'lucide-react';
import { cn } from '../../utils/cn';
import { type AnnouncementsResponse } from '../../api/announcements';

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
