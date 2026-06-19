// apps/dsa-web/src/components/buyCriteria/CriterionCard.tsx
import { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, LoaderCircle, Circle } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { CriterionResult, CriterionStatus } from '../../api/buyCriteria';

interface CriterionCardProps {
  criterionId: string;
  criterionName: string;
  index: number;
  status: CriterionStatus;
  result: CriterionResult | null;
}

const STATUS_CONFIG: Record<CriterionStatus, { border: string; bg: string; iconColor: string }> = {
  idle: { border: 'border-slate-200', bg: 'bg-slate-50', iconColor: 'text-slate-300' },
  running: { border: 'border-cyan-300', bg: 'bg-cyan-50/50', iconColor: 'text-cyan-500' },
  pass: { border: 'border-emerald-300', bg: 'bg-white', iconColor: 'text-emerald-500' },
  fail: { border: 'border-rose-300', bg: 'bg-white', iconColor: 'text-rose-500' },
  not_evaluated: { border: 'border-slate-200', bg: 'bg-slate-50/50', iconColor: 'text-slate-300' },
};

const NUM_LABELS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧'];

export function CriterionCard({ criterionName, index, status, result }: CriterionCardProps) {
  const [dataExpanded, setDataExpanded] = useState(false);
  const config = STATUS_CONFIG[status];

  return (
    <div
      className={cn(
        'rounded-2xl border-2 transition-all duration-300',
        config.border,
        config.bg,
        status === 'not_evaluated' && 'opacity-50',
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-3 p-4">
        {/* Status icon */}
        <div className="flex-shrink-0 mt-0.5">
          {status === 'idle' && <Circle className={cn('h-6 w-6', config.iconColor)} />}
          {status === 'running' && <LoaderCircle className={cn('h-6 w-6 animate-spin', config.iconColor)} />}
          {status === 'pass' && <CheckCircle2 className={cn('h-6 w-6', config.iconColor)} />}
          {status === 'fail' && <XCircle className={cn('h-6 w-6', config.iconColor)} />}
          {status === 'not_evaluated' && <Circle className={cn('h-6 w-6', config.iconColor)} />}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="text-[15px] font-semibold text-slate-800">
              {NUM_LABELS[index]} {criterionName}
            </span>
            {status === 'pass' && (
              <span className="text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full">
                通过
              </span>
            )}
            {status === 'fail' && (
              <span className="text-xs font-medium text-rose-700 bg-rose-50 px-2.5 py-0.5 rounded-full">
                未通过
              </span>
            )}
            {status === 'running' && (
              <span className="text-xs font-medium text-cyan-700 bg-cyan-50 px-2.5 py-0.5 rounded-full">
                分析中...
              </span>
            )}
            {status === 'not_evaluated' && (
              <span className="text-xs text-slate-400">未评估</span>
            )}
          </div>

          {/* Verdict or status text */}
          {status === 'pass' && result && (
            <p className="text-[13px] text-slate-600 leading-relaxed">{result.verdict}</p>
          )}
          {status === 'fail' && result && (
            <div className={result.verdict.includes('评估失败')
              ? 'rounded-lg bg-amber-50 border border-amber-200 p-2.5 text-[13px] text-amber-800 leading-relaxed'
              : 'text-[13px] text-slate-600 leading-relaxed'
            }>
              {result.verdict.includes('评估失败') && (
                <span className="font-medium mr-1">⚠️ 技术错误：</span>
              )}
              {result.verdict}
            </div>
          )}
          {status === 'running' && (
            <p className="text-[13px] text-slate-400">正在采集数据并分析...</p>
          )}
          {status === 'idle' && (
            <p className="text-[13px] text-slate-400">等待分析</p>
          )}
          {status === 'not_evaluated' && (
            <p className="text-[13px] text-slate-400">前置准则未通过，未评估</p>
          )}

          {/* Collapsible data details (only when result exists) */}
          {result && (status === 'pass' || status === 'fail') && (
            <div className="mt-2 border-t border-slate-100 pt-2">
              <button
                onClick={() => setDataExpanded(!dataExpanded)}
                className="flex items-center gap-1 text-xs text-cyan-600 hover:text-cyan-700 transition-colors"
              >
                {dataExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                查看底层数据明细
              </button>
              {dataExpanded && (
                <div className="mt-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 font-mono leading-relaxed max-h-64 overflow-y-auto">
                  <pre className="whitespace-pre-wrap break-all">
                    {JSON.stringify(result.evidence.raw_data, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
