import type React from 'react';
import { Activity, CheckCircle2, Clock3, Database, ListChecks, ThumbsDown, ThumbsUp, TriangleAlert } from 'lucide-react';
import type { AgentRunDetail } from '../../api/runExplorer';
import { Badge, Card } from '../common';
import { cn } from '../../utils/cn';
import { formatDateTime } from '../../utils/format';

type RunDetailContentProps = {
  detail: AgentRunDetail;
  onFeedback: (rating: -1 | 1) => void;
};

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', running: '运行中', recovering: '恢复中', interrupted: '等待审批',
  completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消', blocked: '已阻止',
  succeeded: '已完成',
};

const DIMENSION_LABELS: Record<string, string> = {
  control_loop: '分析过程', controlLoop: '分析过程', execution: '执行完成',
  evidence_links: '资料对应', evidenceLinks: '资料对应', answer_contract: '回答完整度',
  answerContract: '回答完整度', budget: '运行资源',
};

const statusVariant = (status: string) => {
  if (status === 'completed' || status === 'succeeded') return 'success' as const;
  if (status === 'running' || status === 'recovering' || status === 'queued') return 'info' as const;
  if (status === 'partial' || status === 'blocked' || status === 'interrupted') return 'warning' as const;
  return 'danger' as const;
};

const text = (value: unknown) => (typeof value === 'string' ? value : value == null ? '' : String(value));
const percent = (value?: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`);
const formatDuration = (durationMs?: number | null) => {
  if (durationMs == null) return '—';
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} 秒`;
  return `${Math.floor(durationMs / 60_000)} 分 ${Math.round((durationMs % 60_000) / 1000)} 秒`;
};
const stringList = (value: unknown) => (Array.isArray(value) ? value.map(text).filter(Boolean) : []);
const uniqueStrings = (values: string[]) => Array.from(new Set(values));
const isHttpUrl = (value: string) => /^https?:\/\//i.test(value);
const sourceLabel = (value: string) => {
  const trimmed = value.trim();
  if (!isHttpUrl(trimmed)) return trimmed.replace(/^www\./i, '');
  try {
    return new URL(trimmed).hostname.replace(/^www\./i, '');
  } catch {
    return trimmed;
  }
};
const displayDataTime = (value: unknown) => (text(value) ? formatDateTime(text(value)) : '时间未提供');
const violationLabel = (code: string) => ({
  execution_contract_failed: '部分执行步骤没有完整结束',
  evidence_link_contract_failed: '部分结论没有完成逐条资料对应',
  answer_contract_failed: '回答内容没有完全满足要求',
  control_loop_contract_failed: '分析过程存在异常或缺少步骤',
  budget_contract_failed: '本次分析触及运行资源上限',
}[code] ?? code.replaceAll('_', ' '));

export function RunDetailContent({ detail, onFeedback }: RunDetailContentProps) {
  const run = detail.snapshot.run ?? {};
  const projection = detail.snapshot.qualityProjection ?? {};
  const toolResults = projection.toolResults ?? [];
  const evidence = projection.evidence ?? [];
  const toolNames = uniqueStrings(toolResults.map((item) => text(item.toolName)).filter(Boolean));
  const sourceNames = uniqueStrings(evidence.flatMap((item) => stringList(item.sourceRefs)));
  const sourceLabels = sourceNames.length > 0 ? uniqueStrings(sourceNames.map(sourceLabel)) : toolNames;
  const feedbackRating = detail.snapshot.feedback?.rating;
  const durationMs = text(run.startedAt) && text(run.finishedAt)
    ? new Date(text(run.finishedAt)).getTime() - new Date(text(run.startedAt)).getTime()
    : null;

  return (
    <div className="max-h-[calc(100vh-9rem)] space-y-3 overflow-y-auto pr-1">
      <Card padding="none" className="rounded-xl p-3">
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-1.5">
              <h2 className="text-sm font-semibold text-foreground">回答结果</h2>
              <Badge variant={statusVariant(text(run.status))}>{STATUS_LABELS[text(run.status)] ?? text(run.status)}</Badge>
            </div>
            <p className="mt-1.5 text-xs text-secondary-text">生成于 {formatDateTime(text(run.createdAt))}</p>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-secondary-text">这次结果有帮助吗？</span>
            <button type="button" onClick={() => onFeedback(1)} className={cn('rounded-md border p-1.5 transition', feedbackRating === 1 ? 'border-success/40 bg-success/10 text-success' : 'border-border text-secondary-text hover:text-success')} aria-label="有帮助"><ThumbsUp className="size-3.5" /></button>
            <button type="button" onClick={() => onFeedback(-1)} className={cn('rounded-md border p-1.5 transition', feedbackRating === -1 ? 'border-danger/40 bg-danger/10 text-danger' : 'border-border text-secondary-text hover:text-danger')} aria-label="没帮助"><ThumbsDown className="size-3.5" /></button>
          </div>
        </div>
        <div className="mt-3 rounded-lg border border-border/70 bg-muted/35 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs font-semibold text-foreground">助手给出的结论</p>
            <span className={cn('text-[11px] font-medium', detail.score.passed ? 'text-success' : 'text-warning')}>
              {detail.score.passed ? '资料核对通过' : `有 ${detail.score.violations.length} 个待核对问题`}
            </span>
          </div>
          <div className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap text-xs leading-6 text-foreground/85">{text(run.finalText) || '尚未生成最终回答。'}</div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric icon={<Clock3 className="size-3.5 text-cyan" />} label="耗时" value={formatDuration(durationMs)} />
          <Metric icon={<ListChecks className="size-3.5 text-purple" />} label="资料返回" value={String(toolResults.length)} />
          <Metric icon={<Database className="size-3.5 text-emerald-600" />} label="资料入口" value={String(toolNames.length)} />
          <Metric icon={<Activity className="size-3.5 text-warning" />} label="核对分" value={percent(detail.score.totalScore)} />
        </div>
      </Card>

      <Card padding="none" className="rounded-xl p-3" title="参考资料" subtitle="回答依据">
        <p className="text-xs text-secondary-text">
          本次回答关联 {sourceLabels.length} 个来源{sourceNames.length > sourceLabels.length ? `（${sourceNames.length} 条原始引用）` : ''}，整理成 {evidence.length} 条可核对证据。
        </p>
        {sourceLabels.length > 0 ? <div className="mt-2 flex flex-wrap gap-1">{sourceLabels.map((source) => <span key={source} className="rounded-md bg-cyan/8 px-2 py-1 text-[11px] text-cyan" title={source}>{source}</span>)}</div> : null}
        <div className="mt-2 divide-y divide-border/70 rounded-lg border border-border/70">
          {evidence.map((item, index) => <EvidenceRow key={text(item.evidenceId) || text(item.id) || index} item={item} index={index} />)}
          {evidence.length === 0 ? <p className="px-2.5 py-2 text-xs text-secondary-text">本次回答没有可展示的资料记录。</p> : null}
        </div>
      </Card>

      <Card padding="none" className="rounded-xl p-3" title="结果核对" subtitle="自动检查回答与资料是否对应">
        <div className={cn('rounded-lg px-3 py-2', detail.score.passed ? 'bg-success/8 text-success' : 'bg-warning/10 text-warning')}>
          <p className="text-xs font-semibold">{detail.score.passed ? '这次回答的资料核对已通过' : '这次回答还有内容需要核对'}</p>
          <p className="mt-0.5 text-[11px] opacity-85">{detail.score.passed ? '回答中的事实已经找到对应的资料记录。' : '下面列出未完全满足的检查项，阅读结论时请优先关注这些部分。'}</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {Object.entries(detail.score.dimensions).map(([name, dimension]) => <div key={name} className="rounded-lg border border-border/70 p-2.5"><div className="flex items-center justify-between"><span className="text-xs font-medium">{DIMENSION_LABELS[name] ?? name}</span><span className={cn('text-xs font-semibold', dimension.score >= 0.85 ? 'text-success' : 'text-warning')}>{percent(dimension.score)}</span></div><div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted"><div className={cn('h-full rounded-full', dimension.score >= 0.85 ? 'bg-success' : 'bg-warning')} style={{ width: `${Math.round(dimension.score * 100)}%` }} /></div></div>)}
        </div>
        {detail.score.violations.length > 0 ? <div className="mt-3 space-y-1.5">{detail.score.violations.map((violation, index) => <div key={`${violation.code}-${index}`} className="flex items-start gap-2 rounded-md bg-warning/8 px-2.5 py-1.5 text-xs text-warning"><TriangleAlert className="mt-0.5 size-3.5 shrink-0" /><span>{violationLabel(violation.code)}</span></div>)}</div> : <div className="mt-3 flex items-center gap-2 text-xs text-success"><CheckCircle2 className="size-3.5" />暂未发现明显的资料关联问题</div>}
      </Card>

      <Card padding="none" className="rounded-xl p-3" title="资料获取过程" subtitle="本次回答实际使用的资料入口">
        <div className="space-y-2">
          {toolResults.map((result, index) => {
            const actionId = text(result.actionId) || text(result.toolCallId);
            const actionEvidence = evidence.filter((item) => text(item.actionId) === text(result.actionId));
            const actionSources = uniqueStrings(actionEvidence.flatMap((item) => stringList(item.sourceRefs)).map(sourceLabel));
            return <div key={actionId || index} className="rounded-lg border border-border/70 p-2.5"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-xs font-medium">{text(result.toolName) || '未指定工具'}</p><p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={actionId}>调用编号 {actionId}</p></div><Badge variant={result.success === true ? 'success' : 'danger'}>{result.success === true ? '成功' : '失败'}</Badge></div><p className="mt-2 line-clamp-2 text-[11px] text-foreground/75">{actionSources.join('、') || '来源未标注'} · {actionEvidence.length} 条证据</p><div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-secondary-text"><span>类型：{text(result.effect) === 'side_effect' ? '外部操作' : '读取资料'}</span><span>数据时间：{displayDataTime(result.dataTime || actionEvidence[0]?.dataTime)}</span></div></div>;
          })}
          {toolResults.length === 0 ? <p className="text-xs text-secondary-text">该问题没有调用工具。</p> : null}
        </div>
      </Card>
    </div>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="rounded-lg bg-muted/60 p-2.5">{icon}<p className="mt-1 text-[11px] text-secondary-text">{label}</p><p className="mt-0.5 text-sm font-medium">{value}</p></div>;
}

function EvidenceRow({ item, index }: { item: Record<string, unknown>; index: number }) {
  const refs = stringList(item.sourceRefs);
  const sources = uniqueStrings(refs.map(sourceLabel));
  const urls = refs.filter(isHttpUrl);
  const evidenceId = text(item.evidenceId) || text(item.id) || `证据 ${index + 1}`;
  return <div className="px-2.5 py-2"><div className="flex items-center justify-between gap-2"><span className="line-clamp-2 text-xs font-medium text-foreground" title={refs.join('、')}>{sources.join('、') || text(item.toolName) || '未标注来源'}</span><span className="shrink-0 text-[10px] text-secondary-text">{displayDataTime(item.dataTime)}</span></div><p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={evidenceId}>{evidenceId}</p>{urls.length > 0 ? <details className="mt-1.5 text-[10px] text-secondary-text"><summary className="cursor-pointer select-none hover:text-foreground">查看 {urls.length} 条原始链接</summary><div className="mt-1 space-y-0.5 border-l border-border pl-2">{urls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer" className="block break-all text-cyan hover:underline">{url}</a>)}</div></details> : null}</div>;
}

export default RunDetailContent;
