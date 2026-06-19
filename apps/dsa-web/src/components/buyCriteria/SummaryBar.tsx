// apps/dsa-web/src/components/buyCriteria/SummaryBar.tsx
import { CheckCircle2, XCircle, Circle, RefreshCw, Play } from 'lucide-react';
import { Button } from '../common/Button';
import { cn } from '../../utils/cn';
import { CRITERIA_ORDER } from '../../api/buyCriteria';
import type { CriterionStatus } from '../../api/buyCriteria';

interface SummaryBarProps {
  symbol: string;
  stockName: string;
  criteriaStatuses: Record<string, CriterionStatus>;
  finalDecision: '可买入' | '不可买入' | null;
  summary: string;
  isRunning: boolean;
  onStart: () => void;
  onRestart: () => void;
}

export function SummaryBar({
  symbol,
  stockName,
  criteriaStatuses,
  finalDecision,
  summary,
  isRunning,
  onStart,
  onRestart,
}: SummaryBarProps) {
  const passed = Object.values(criteriaStatuses).filter(s => s === 'pass').length;
  const failed = Object.values(criteriaStatuses).filter(s => s === 'fail').length;
  const notEvaluated = Object.values(criteriaStatuses).filter(s => s === 'not_evaluated').length;
  const hasResults = passed > 0 || failed > 0;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        {/* Left: stock info + counts */}
        <div className="flex items-center gap-4">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-400">
              买入判定
            </div>
            <div className="text-lg font-bold text-slate-800 mt-0.5">
              {stockName || symbol}
            </div>
          </div>

          {hasResults && (
            <>
              <div className="h-9 w-px bg-slate-200" />
              <div className="flex gap-1.5">
                <span className={cn(
                  'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium',
                  passed > 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-50 text-slate-400',
                )}>
                  <CheckCircle2 className="h-3.5 w-3.5" /> {passed}
                </span>
                <span className={cn(
                  'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium',
                  failed > 0 ? 'bg-rose-50 text-rose-700' : 'bg-slate-50 text-slate-400',
                )}>
                  <XCircle className="h-3.5 w-3.5" /> {failed}
                </span>
                {notEvaluated > 0 && (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-50 text-slate-400">
                    <Circle className="h-3.5 w-3.5" /> {notEvaluated}
                  </span>
                )}
              </div>
            </>
          )}
        </div>

        {/* Right: decision + actions */}
        <div className="flex items-center gap-3">
          {finalDecision && (
            <span className={cn(
              'text-sm font-semibold',
              finalDecision === '可买入' ? 'text-emerald-600' : 'text-rose-600',
            )}>
              {finalDecision === '可买入' ? '✅' : '❌'} {finalDecision}
            </span>
          )}
          {summary && !isRunning && (
            <span className="text-xs text-slate-500">{summary}</span>
          )}
          {!hasResults && !isRunning && (
            <Button onClick={onStart} size="sm">
              <Play className="h-4 w-4 mr-1" />
              开始分析
            </Button>
          )}
          {hasResults && !isRunning && (
            <Button onClick={onRestart} variant="outline" size="sm">
              <RefreshCw className="h-4 w-4 mr-1" />
              重新分析
            </Button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="flex gap-1 mt-3">
        {CRITERIA_ORDER.map((c) => {
          const status = criteriaStatuses[c.id];
          return (
            <div
              key={c.id}
              className={cn(
                'h-1 flex-1 rounded-full transition-colors duration-300',
                status === 'pass' && 'bg-emerald-400',
                status === 'fail' && 'bg-rose-400',
                status === 'running' && 'bg-cyan-400 animate-pulse',
                status === 'idle' && 'bg-slate-200',
                status === 'not_evaluated' && 'bg-slate-100',
              )}
            />
          );
        })}
      </div>
    </div>
  );
}
