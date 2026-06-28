import { motion } from 'motion/react';
import { Play, Pause, Square } from 'lucide-react';
import { Button } from '../common';

interface BatchProgressPanelProps {
  isRunning: boolean;
  isPaused: boolean;
  isStopping: boolean;
  runCompleted: number;
  runStockCount: number;
  runSuccess: number;
  runFailed: number;
  currentStock: string | null;
  currentMessage: string | null;
  onTogglePause: () => void;
  onStop: () => void;
}

export function BatchProgressPanel({
  isRunning,
  isPaused,
  isStopping,
  runCompleted,
  runStockCount,
  runSuccess,
  runFailed,
  currentStock,
  currentMessage,
  onTogglePause,
  onStop,
}: BatchProgressPanelProps) {
  if (!isRunning) return null;

  const progressPercent = runStockCount > 0
    ? Math.round((runCompleted / runStockCount) * 100)
    : 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-text">
          {currentMessage || (currentStock ? `分析中: ${currentStock}` : '准备中...')}
        </span>
        <span className="font-mono text-foreground tabular-nums">
          {runCompleted}/{runStockCount} ({progressPercent}%)
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-hover">
        <motion.div
          className="h-full rounded-full bg-primary"
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(progressPercent, 100)}%` }}
          transition={{ duration: 0.3 }}
        />
      </div>
      <div className="flex gap-3 text-[10px] text-muted-text">
        <span className="text-emerald-600 dark:text-emerald-400">成功 {runSuccess}</span>
        <span className="text-red-600 dark:text-red-400">失败 {runFailed}</span>
        {isPaused && <span className="text-amber-600 dark:text-amber-400">已暂停</span>}
        {isStopping && <span className="text-amber-600 dark:text-amber-400">终止中</span>}
      </div>
      <div className="flex gap-2 pt-1">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onTogglePause}
          disabled={isStopping}
          className="flex-1"
        >
          {isPaused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          {isPaused ? '继续' : '暂停'}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onStop}
          disabled={isStopping}
          className="flex-1 text-red-600 hover:text-red-700 dark:text-red-400"
        >
          <Square className="h-3.5 w-3.5" />
          终止
        </Button>
      </div>
    </div>
  );
}