import React, { useCallback, useState } from 'react';
import { Zap } from 'lucide-react';
import { stocksApi } from '../api/stocks';
import { runAtrScreener, SCREEN_GROUP_NAME, type AtrScreenResult } from '../utils/atr-screener';
import type { WatchlistGroup } from '../utils/watchlistGroups';

interface AtrScreenerBarProps {
  groups: WatchlistGroup[];
  onGroupsChange: (fn: (prev: WatchlistGroup[]) => WatchlistGroup[]) => void;
  onError: (msg: string) => void;
  onSuccess: (msg: string) => void;
  onGroupSelect: (groupId: string) => void;
}

const AtrScreenerBar: React.FC<AtrScreenerBarProps> = ({
  groups,
  onGroupsChange,
  onError,
  onSuccess,
  onGroupSelect,
}) => {
  const [isScreening, setIsScreening] = useState(false);
  const [progress, setProgress] = useState('');
  const [result, setResult] = useState<AtrScreenResult | null>(null);

  const handleScreen = useCallback(async () => {
    setIsScreening(true);
    setProgress('正在加载 K 线数据...');

    try {
      const data = await stocksApi.getAtrScreenerKlines();
      setProgress(`已获取 ${data.qualified_stocks} 只股票数据，正在计算 ATR 指标...`);
      await new Promise((r) => setTimeout(r, 50));

      const screenResult = runAtrScreener(data.klines);
      setResult(screenResult);

      const matchedCodes = screenResult.matchedCodes;
      const existingIndex = groups.findIndex((g) => g.name === SCREEN_GROUP_NAME);

      if (existingIndex >= 0) {
        onGroupsChange((prev) =>
          prev.map((g, i) => (i === existingIndex ? { ...g, codes: matchedCodes } : g)),
        );
        const group = groups[existingIndex];
        if (group) onGroupSelect(group.id);
      } else {
        const newGroup: WatchlistGroup = {
          id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          name: SCREEN_GROUP_NAME,
          codes: matchedCodes,
        };
        onGroupsChange((prev) => [...prev, newGroup]);
        setTimeout(() => onGroupSelect(newGroup.id), 0);
      }

      setProgress(`筛选完成: 共分析 ${screenResult.totalAnalyzed} 只，${matchedCodes.length} 只符合条件`);
      onSuccess(`ATR 高波动选股: ${matchedCodes.length} 只`);
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : 'ATR 选股失败');
    } finally {
      setIsScreening(false);
    }
  }, [groups, onGroupsChange, onError, onSuccess, onGroupSelect]);

  return (
    <div className="shrink-0 flex items-center gap-3 rounded-2xl border border-orange-200 bg-orange-50/80 px-4 py-2.5 shadow-sm">
      <Zap className="h-4 w-4 text-orange-500" />
      <span className="text-xs text-orange-700">
        {progress || 'ATR 相对波动率选股：250 日内 ATR%>2.8 的天数占比 ≥ 60%'}
      </span>
      {isScreening && (
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-orange-200 border-t-orange-500" />
      )}
      {!isScreening && (
        <button
          type="button"
          onClick={handleScreen}
          className="ml-auto rounded-lg bg-orange-500 px-3 py-1 text-xs font-medium text-white transition hover:bg-orange-600"
        >
          {result ? '重新筛选' : '开始筛选'}
        </button>
      )}
    </div>
  );
};

export default AtrScreenerBar;
