import { Plus, Zap } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { WatchlistGroup } from '../../utils/watchlistGroups';
import type { WatchlistResponse } from '../../api/watchlist';
import { DEFAULT_GROUP_ID } from '../../hooks/useWatchlistManage';

const DEFAULT_GROUP_NAME = '我的自选股';

interface GroupTabsProps {
  groups: WatchlistGroup[];
  activeGroupId: string;
  watchlist: WatchlistResponse | null;
  onChangeGroup: (groupId: string) => void;
  onQuickAddGroup: () => void;
  onOpenScreener: () => void;
}

export default function GroupTabs({
  groups,
  activeGroupId,
  watchlist,
  onChangeGroup,
  onQuickAddGroup,
  onOpenScreener,
}: GroupTabsProps) {
  return (
    <div className="shrink-0 flex items-center justify-between gap-2 rounded-2xl border border-slate-200 bg-white/88 px-3 py-2 shadow-sm">
      {/* Left: group tabs */}
      <div className="flex items-center gap-1.5 overflow-x-auto">
        {/* Default group tab */}
        <button
          type="button"
          onClick={() => onChangeGroup(DEFAULT_GROUP_ID)}
          className={cn(
            'flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition whitespace-nowrap',
            activeGroupId === DEFAULT_GROUP_ID
              ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200'
              : 'text-slate-600 hover:bg-slate-100',
          )}
        >
          {DEFAULT_GROUP_NAME}
          <span className={cn(
            'rounded-full px-1.5 py-0.5 text-[10px] font-normal',
            activeGroupId === DEFAULT_GROUP_ID ? 'bg-cyan-100 text-cyan-700' : 'bg-slate-100 text-slate-500',
          )}>
            {watchlist?.count || 0}
          </span>
        </button>

        {/* Custom group tabs */}
        {groups.map((group) => (
          <button
            key={group.id}
            type="button"
            onClick={() => onChangeGroup(group.id)}
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-medium transition whitespace-nowrap',
              activeGroupId === group.id
                ? 'bg-cyan-50 text-cyan-800 ring-1 ring-cyan-200'
                : 'text-slate-600 hover:bg-slate-100',
            )}
          >
            {group.name}
            <span className={cn(
              'rounded-full px-1.5 py-0.5 text-[10px] font-normal',
              activeGroupId === group.id ? 'bg-cyan-100 text-cyan-700' : 'bg-slate-100 text-slate-500',
            )}>
              {group.codes.length}
            </span>
          </button>
        ))}
        {/* + 新建 group button */}
        <button
          type="button"
          onClick={onQuickAddGroup}
          className="flex shrink-0 items-center gap-1 rounded-xl border border-dashed border-slate-300 px-3 py-2 text-sm text-slate-400 transition hover:border-cyan-300 hover:text-cyan-600"
        >
          <Plus className="h-4 w-4" />
          新建
        </button>
      </div>
      {/* Right: screening button */}
      <button
        type="button"
        onClick={onOpenScreener}
        className="flex shrink-0 items-center gap-1 rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-sm font-medium text-orange-600 transition hover:border-orange-300 hover:bg-orange-100"
        aria-label="ATR 相对波动率选股"
      >
        <Zap className="h-4 w-4" />
        筛选
      </button>
    </div>
  );
}