import { useCallback } from 'react';
import { useWatchlistGroups } from '../../hooks/useWatchlistGroups';

interface BatchStockScopeProps {
  baseCount: number;
  selectedGroupId: string;
  onGroupChange: (groupId: string) => void;
}

export function BatchStockScope({
  baseCount,
  selectedGroupId,
  onGroupChange,
}: BatchStockScopeProps) {
  const { groups } = useWatchlistGroups();

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => onGroupChange(e.target.value),
    [onGroupChange],
  );

  if (groups.length === 0) return null;

  return (
    <div className="space-y-1">
      <label className="text-[10px] font-medium text-muted-text uppercase tracking-wider">
        跑批范围
      </label>
      <select
        value={selectedGroupId}
        onChange={handleChange}
        className="w-full rounded-lg border border-subtle bg-surface px-3 py-2 text-xs text-foreground focus:border-primary/40 focus:outline-none focus:ring-1 focus:ring-primary/20"
      >
        <option value="all">全部自选股 ({baseCount})</option>
        {groups.map((group) => (
          <option key={group.id} value={group.id}>
            {group.name} ({group.codes.length})
          </option>
        ))}
      </select>
    </div>
  );
}