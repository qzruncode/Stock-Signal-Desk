import { Play, Clock } from 'lucide-react';
import type { BatchAnalysisMode } from '../../api/batch';
import { Button } from '../common';

interface BatchControlBarProps {
  isRunning: boolean;
  stockCount: number;
  analysisMode: BatchAnalysisMode;
  onTrigger: () => void;
  onOpenSchedule: () => void;
}

export function BatchControlBar({
  isRunning,
  stockCount,
  analysisMode,
  onTrigger,
  onOpenSchedule,
}: BatchControlBarProps) {
  return (
    <div className="flex gap-2">
      <Button
        type="button"
        variant="primary"
        size="sm"
        onClick={onTrigger}
        disabled={isRunning || stockCount === 0}
        isLoading={isRunning}
        loadingText="运行中"
        className="flex-1"
      >
        <Play className="h-3.5 w-3.5" />
        {analysisMode === 'buy_criteria' ? '买入判断筛选' : '跑批'} ({stockCount} 只)
      </Button>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={onOpenSchedule}
      >
        <Clock className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}