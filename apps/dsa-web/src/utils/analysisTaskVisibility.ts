import type { TaskInfo } from '../types/analysis';

export function isFloatingAnalysisTaskVisible(task: TaskInfo): boolean {
  if (task.reportType === 'market_mainline_report') return false;
  return (
    task.status === 'pending'
    || task.status === 'processing'
    || task.status === 'completed'
    || task.status === 'failed'
  );
}
