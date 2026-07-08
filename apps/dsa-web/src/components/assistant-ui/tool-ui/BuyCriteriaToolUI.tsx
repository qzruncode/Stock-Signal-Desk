import { lazy, Suspense, useContext, useState } from 'react';
import { CheckCircle2Icon, XCircleIcon, ChevronDownIcon, ShieldAlertIcon } from 'lucide-react';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import { cn } from '../../../utils/cn';
import { type BuyCriteriaToolResult, type BuyCriteriaItem } from '../../../utils/toolResults';
import { ToolStatusPill } from './shared';
import { ApprovalContext } from './ApprovalContext';

// recharts 体积大,懒加载到独立 chunk,仅在实际渲染买入判定时加载。
const BuyCriteriaRadar = lazy(() => import('./BuyCriteriaRadar'));

const NUM_LABELS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧'];

/**
 * 买入判定内联可视化:8 维雷达图 + 结论卡 + 各维度裁决。
 * 执行前需 HITL 确认(8 次 LLM 调用),确认按钮在此渲染。
 */
const BuyCriteriaToolUI = ({
  args,
  result,
  status,
  isError,
  toolCallId,
}: ToolCallMessagePartProps<{ symbol: string; skip_cache?: boolean }, BuyCriteriaToolResult>) => {
  const { pendingApprovals, approveToolCall } = useContext(ApprovalContext);
  const approval = pendingApprovals[toolCallId];

  // HITL: 后端发了 approval-request,等待用户确认才执行
  if (approval && status.type === 'running' && !result) {
    return (
      <div className="my-2 rounded-xl border-2 border-amber-300 bg-amber-50 px-3 py-2.5">
        <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
          <ShieldAlertIcon className="size-4 shrink-0" />
          <span>买入判定需要确认</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-amber-700">{approval.reason}</p>
        <p className="mt-0.5 text-[11px] text-amber-600/80">股票: {approval.symbol || args.symbol}</p>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={() => approveToolCall(toolCallId, true)}
            className="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-600"
          >
            确认执行
          </button>
          <button
            type="button"
            onClick={() => approveToolCall(toolCallId, false)}
            className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100"
          >
            取消
          </button>
        </div>
      </div>
    );
  }

  if (status.type === 'running' && !result) {
    return <ToolStatusPill status={status} isError={isError} label={`正在执行 ${args.symbol} 8 维买入判定…(预计 ~30s)`} />;
  }
  if ((isError || (status.type === 'incomplete' && status.reason === 'error')) && !result) {
    return <ToolStatusPill status={status} isError label="买入判定失败" />;
  }
  if (!result || !result.criteria?.length) {
    return <ToolStatusPill status={status} isError label="无判定结果" />;
  }

  const ordered = [...result.criteria].sort((a, b) => a.index - b.index);
  const isBuy = result.final_decision === '可买入';

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border bg-card/60">
      {/* 结论卡 */}
      <div className={cn('flex items-center justify-between gap-3 border-b px-3 py-2.5', isBuy ? 'border-emerald-200 bg-emerald-50/60' : 'border-red-200 bg-red-50/60')}>
        <div className="flex items-center gap-2">
          {isBuy ? <CheckCircle2Icon className="size-5 text-emerald-500" /> : <XCircleIcon className="size-5 text-red-500" />}
          <div>
            <div className="text-sm font-bold text-foreground">{result.stock_name ?? args.symbol} · 买入判定</div>
            <div className={cn('text-xs font-medium', isBuy ? 'text-emerald-700' : 'text-red-700')}>
              {result.final_decision} · 通过 {result.passed_count} / 失败 {result.failed_count}
              {result.not_evaluated_count > 0 && ` / 未评估 ${result.not_evaluated_count}`}
            </div>
          </div>
        </div>
        {result.cached && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">今日缓存</span>}
      </div>

      <div className="grid gap-3 px-3 py-3 lg:grid-cols-[260px_1fr]">
        {/* 雷达图(懒加载) */}
        <div className="h-[260px] w-full">
          <Suspense fallback={<div className="flex h-full items-center justify-center text-xs text-muted-foreground">加载图表…</div>}>
            <BuyCriteriaRadar criteria={ordered} isBuy={isBuy} />
          </Suspense>
        </div>

        {/* 维度裁决列表 */}
        <div className="space-y-1.5">
          {result.summary && <p className="text-xs leading-5 text-muted-foreground">{result.summary}</p>}
          {ordered.map((c, idx) => (
            <CriterionRow key={c.criterion_id} item={c} index={idx} />
          ))}
        </div>
      </div>
    </div>
  );
};

const CriterionRow: React.FC<{ item: BuyCriteriaItem; index: number }> = ({ item, index }) => {
  const [expanded, setExpanded] = useState(false);
  const pass = item.passed;
  return (
    <div className={cn('rounded-lg border bg-card px-2.5 py-1.5', pass ? 'border-emerald-200' : 'border-red-200')}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="text-[10px] font-semibold text-muted-foreground">{NUM_LABELS[index] ?? index + 1}</span>
        {pass ? <CheckCircle2Icon className="size-3.5 shrink-0 text-emerald-500" /> : <XCircleIcon className="size-3.5 shrink-0 text-red-500" />}
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{item.criterion_name}</span>
        <span className={cn('shrink-0 text-[10px] font-medium', pass ? 'text-emerald-700' : 'text-red-700')}>{pass ? '通过' : '未通过'}</span>
        <ChevronDownIcon className={cn('size-3 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-180')} />
      </button>
      {expanded && (
        <div className="mt-1.5 space-y-1 border-t border-border pt-1.5">
          {item.verdict && <p className="text-[11px] leading-5 text-foreground/80 [overflow-wrap:anywhere]">{item.verdict}</p>}
          {item.evidence?.data_summary && (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-muted p-2 text-[10px] leading-4 text-muted-foreground [overflow-wrap:anywhere]">{item.evidence.data_summary}</pre>
          )}
        </div>
      )}
    </div>
  );
};

export default BuyCriteriaToolUI;
