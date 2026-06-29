import { FileText, Layers3, RefreshCw, Sparkles } from 'lucide-react';
import { ApiErrorAlert, Button, EmptyState } from '../common';
import { DashboardStateBlock } from '../dashboard';
import { ConversationReport } from '../report';
import type { AnalysisReport } from '../../types/analysis';
import type { ParsedApiError } from '../../api/error';

interface HomeAnalysisCanvasProps {
  error: ParsedApiError | null;
  onClearError: () => void;
  isLoadingTaskStatus: boolean;
  taskPreviewReport: AnalysisReport | null;
  isLoadingReport: boolean;
  pendingAutoSelectCode: string | null;
  selectedReport: AnalysisReport | null;
  isAnalyzing: boolean;
  onReanalyze: () => void;
  onOpenMarkdownDrawer: () => void;
  reanalyzeLabel: string;
  fullReportLabel: string;
}

export default function HomeAnalysisCanvas({
  error,
  onClearError,
  isLoadingTaskStatus,
  taskPreviewReport,
  isLoadingReport,
  pendingAutoSelectCode,
  selectedReport,
  isAnalyzing,
  onReanalyze,
  onOpenMarkdownDrawer,
  reanalyzeLabel,
  fullReportLabel,
}: HomeAnalysisCanvasProps) {
  let content;

  if (isLoadingTaskStatus) {
    content = (
      <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-xl border border-slate-200 bg-white/82">
        <DashboardStateBlock title="加载任务对话中..." loading />
      </div>
    );
  } else if (taskPreviewReport) {
    content = (
      <div className="space-y-4 pb-8">
        <ConversationReport data={taskPreviewReport} isHistory />
      </div>
    );
  } else if (isLoadingReport || pendingAutoSelectCode) {
    content = (
      <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-xl border border-slate-200 bg-white/82">
        <DashboardStateBlock
          title={pendingAutoSelectCode ? `正在为 ${pendingAutoSelectCode} 生成分析报告...` : '加载报告中...'}
          loading
        />
      </div>
    );
  } else if (selectedReport) {
    content = (
      <div className="space-y-4 pb-8">
        <ConversationReport data={selectedReport} isHistory />
      </div>
    );
  } else {
    content = (
      <div className="flex min-h-[26rem] items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/70">
        <EmptyState
          title="开始分析"
          description="输入股票代码进行分析，或从任务控制台选择历史报告查看。"
          className="max-w-xl border-dashed"
          icon={<Sparkles className="h-6 w-6" />}
        />
      </div>
    );
  }

  return (
    <>
      <div className="rounded-xl border border-slate-200 bg-white/88 p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">
              <Layers3 className="h-3.5 w-3.5" />
              Analysis Canvas
            </p>
            <h3 className="mt-1 text-lg font-semibold text-slate-950">AI 对话与报告</h3>
          </div>
          {selectedReport ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                size="sm"
                disabled={isAnalyzing || selectedReport.meta.id === undefined}
                onClick={onReanalyze}
              >
                <RefreshCw className="h-4 w-4" />
                {reanalyzeLabel}
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={selectedReport.meta.id === undefined}
                onClick={onOpenMarkdownDrawer}
              >
                <FileText className="h-4 w-4" />
                {fullReportLabel}
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      {error ? (
        <ApiErrorAlert error={error} className="mb-3" onDismiss={onClearError} />
      ) : null}
      {content}
    </>
  );
}
