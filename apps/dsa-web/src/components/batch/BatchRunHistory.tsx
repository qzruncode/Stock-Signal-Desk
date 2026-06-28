import { FileText, RotateCcw, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { cn } from '../../utils/cn';

interface RunRecord {
  run_id: string;
  template_name: string | null;
  stock_count: number;
  success_count: number;
  fail_count: number;
  status: string;
  completed_at: string | null;
  report_path: string | null;
  results_json: string | null;
}

interface BatchRunHistoryProps {
  runs: RunRecord[];
  isRunning: boolean;
  stockCodes: string[];
  onResume: (runId: string) => void;
  onDelete: (runId: string) => void;
}

function hasPersistedResults(run: RunRecord) {
  if (run.report_path) return true;
  if (!run.results_json) return false;
  return run.results_json !== '[]' && run.results_json !== '{}';
}

function canResumeRun(run: RunRecord) {
  return !run.completed_at && run.success_count + run.fail_count < run.stock_count;
}

export function BatchRunHistory({
  runs,
  isRunning,
  stockCodes,
  onResume,
  onDelete,
}: BatchRunHistoryProps) {
  const navigate = useNavigate();

  if (runs.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <p className="text-[10px] font-medium text-muted-text uppercase tracking-wider">
        跑批记录
      </p>
      <div className="max-h-[200px] overflow-y-auto space-y-1">
        {runs.map((run) => {
          const canOpenReport = hasPersistedResults(run);
          const canResume = canResumeRun(run);
          const statusText = run.status === 'stopped'
            ? '已终止'
            : run.completed_at ? new Date(run.completed_at).toLocaleDateString('zh') : '部分';
          return (
            <div
              key={run.run_id}
              className="flex w-full items-center gap-1 rounded-lg transition-colors hover:bg-hover/70"
            >
              <button
                type="button"
                onClick={() => {
                  if (canOpenReport) {
                    navigate(`/batch/runs/${run.run_id}`);
                  }
                }}
                disabled={!canOpenReport}
                className={cn(
                  'flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-xs',
                  !canOpenReport && 'opacity-50 cursor-default',
                )}
              >
                <FileText className="h-3.5 w-3.5 shrink-0 text-muted-text" />
                <span className="flex-1 truncate">
                  {run.template_name || '未知模板'}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-muted-text">
                  {run.success_count}/{run.stock_count}
                </span>
                <span className="shrink-0 text-[10px] text-muted-text">
                  {statusText}
                </span>
              </button>
              {canResume && (
                <button
                  type="button"
                  aria-label="续跑剩余股票"
                  disabled={isRunning}
                  onClick={() => {
                    if (!isRunning) {
                      onResume(run.run_id);
                    }
                  }}
                  className={cn(
                    'mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-primary transition-colors hover:bg-primary/10',
                    isRunning && 'opacity-40',
                  )}
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                type="button"
                aria-label="删除跑批记录"
                disabled={isRunning}
                onClick={() => {
                  if (!isRunning) {
                    onDelete(run.run_id);
                  }
                }}
                className={cn(
                  'mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-text transition-colors hover:bg-red-500/10 hover:text-red-600',
                  isRunning && 'opacity-40',
                )}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}