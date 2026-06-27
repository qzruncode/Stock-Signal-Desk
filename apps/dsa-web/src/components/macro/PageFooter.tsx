import { Clock } from 'lucide-react';

export function PageFooter({ data }: { data: { _fetched_at: string; _cached: boolean; source: string } }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
      <Clock className="h-3 w-3" />
      <span>
        数据获取时间: {data._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
        {data._cached ? ' · 缓存' : ' · 实时'}
      </span>
      <span className="text-slate-300">|</span>
      <span>数据源: {data.source}</span>
    </div>
  );
}