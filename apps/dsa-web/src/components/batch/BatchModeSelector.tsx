import { useCallback } from 'react';
import type { BatchAnalysisMode } from '../../api/batch';
import { cn } from '../../utils/cn';

const MODES: { value: BatchAnalysisMode; label: string }[] = [
  { value: 'template', label: '模板分析' },
  { value: 'buy_criteria', label: '买入判断筛选' },
];

interface BatchModeSelectorProps {
  analysisMode: BatchAnalysisMode;
  forceRefresh: boolean;
  isRunning: boolean;
  onModeChange: (mode: BatchAnalysisMode) => void;
  onForceRefreshChange: (force: boolean) => void;
}

export function BatchModeSelector({
  analysisMode,
  forceRefresh,
  isRunning,
  onModeChange,
  onForceRefreshChange,
}: BatchModeSelectorProps) {
  const handleModeChange = useCallback(
    (value: BatchAnalysisMode) => {
      if (!isRunning) onModeChange(value);
    },
    [isRunning, onModeChange],
  );

  return (
    <div className="space-y-1">
      <label className="text-[10px] font-medium text-muted-text uppercase tracking-wider">
        分析模式
      </label>
      <div className="grid grid-cols-2 gap-1 rounded-lg border border-subtle bg-surface p-1">
        {MODES.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => handleModeChange(opt.value)}
            disabled={isRunning}
            className={cn(
              'h-8 rounded-md text-xs font-medium transition-colors',
              analysisMode === opt.value
                ? 'bg-primary/10 text-primary'
                : 'text-muted-text hover:bg-hover hover:text-foreground',
              isRunning && 'opacity-50',
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {analysisMode === 'buy_criteria' && (
        <label className="flex items-center gap-2 text-xs text-muted-text">
          <input
            type="checkbox"
            checked={forceRefresh}
            onChange={(e) => onForceRefreshChange(e.target.checked)}
            disabled={isRunning}
            className="h-3.5 w-3.5 rounded border-subtle text-primary focus:ring-primary/20"
          />
          强制重新分析（忽略当日缓存）
        </label>
      )}
    </div>
  );
}