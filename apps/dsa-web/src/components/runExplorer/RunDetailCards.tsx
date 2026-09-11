import { useState } from 'react';
import type React from 'react';
import { Activity, CheckCircle2, Copy, Database, Link2, LoaderCircle, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { AgentBehaviorAudit, AgentSourceSampleResponse } from '../../api/runExplorer';
import { Badge, Card } from '../common';
import { cn } from '../../utils/cn';
import {
  accessModeFor,
  arrayFrom,
  displayDataTime,
  errorStringList,
  field,
  hasValue,
  isHttpUrl,
  jsonString,
  numberValue,
  previewJson,
  record,
  sourceAttemptList,
  sourceLabel,
  stringList,
  text,
  uniqueStrings,
} from './RunDetailUtils';

export function ToolObservationDetails({
  result,
  step,
  actionSources,
  payloadsLoaded,
  payloadsLoading,
  onLoadPayloads,
}: {
  result: Record<string, unknown>;
  step?: Record<string, unknown>;
  actionSources: string[];
  payloadsLoaded: boolean;
  payloadsLoading: boolean;
  onLoadPayloads?: () => void;
}) {
  const stepResult = field(step, ['result']);
  const rawProjectedResult = field(result, ['result', 'response']);
  const displayProjectedResult = field(result, ['displayResult', 'display_result']);
  const projectedResult = rawProjectedResult ?? displayProjectedResult;
  const response = hasValue(stepResult) ? stepResult : projectedResult;
  const responseRecord = record(response);
  const stepArguments = field(step, ['arguments']);
  const request = hasValue(stepArguments)
    ? stepArguments
    : field(result, ['arguments', 'request', 'input']);
  const rawResponse = hasValue(stepResult) ? stepResult : hasValue(projectedResult) ? projectedResult : result;
  const fullResponseLoaded = hasValue(stepResult) || hasValue(rawProjectedResult);
  const toolName = text(field(result, ['toolName', 'tool_name']));
  const accessMode = accessModeFor(toolName, result);
  const accessRecord = record(
    field(responseRecord, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit'])
      ?? field(result, ['contentAccess', 'content_access', 'retrievalAudit', 'retrieval_audit']),
  );
  const contentLength = numberValue(field(accessRecord, ['contentLength', 'content_length']) ?? field(responseRecord, ['contentLength', 'content_length']));
  const extractionMethod = text(field(accessRecord, ['extractionMethod', 'extraction_method']) ?? field(responseRecord, ['extractionMethod', 'extraction_method']));
  const contentRead = field(accessRecord, ['contentRead', 'content_read']);
  const contentExtracted = field(accessRecord, ['contentExtracted', 'content_extracted']);

  const source = text(field(responseRecord, ['source', 'sourceLabel', 'source_label']))
    || text(field(result, ['source', 'sourceLabel', 'source_label']))
    || actionSources[0];
  const sourceKey = text(field(responseRecord, ['sourceKey', 'source_key']))
    || text(field(result, ['sourceKey', 'source_key']));
  const sourceOrigin = text(field(responseRecord, ['sourceOrigin', 'source_origin']))
    || text(field(result, ['sourceOrigin', 'source_origin']));
  const sourceScope = text(field(responseRecord, ['sourceScope', 'source_scope']))
    || text(field(result, ['sourceScope', 'source_scope']));
  const dataTime = field(responseRecord, ['dataTime', 'data_time'])
    ?? field(result, ['dataTime', 'data_time']);
  const dataTimeProvenance = text(field(responseRecord, ['dataTimeProvenance', 'data_time_provenance']))
    || text(field(result, ['dataTimeProvenance', 'data_time_provenance']));
  const rows = arrayFrom(response);
  const count = numberValue(
    field(responseRecord, ['count', 'resultCount', 'result_count', 'total'])
      ?? field(result, ['count', 'resultCount', 'result_count'])
      ?? (rows.length > 0 ? rows.length : undefined),
  );
  const success = field(responseRecord, ['success']) ?? result.success;
  const partial = field(responseRecord, ['partial']) ?? result.partial;
  const fallbackUsed = field(responseRecord, ['fallbackUsed', 'fallback_used'])
    ?? field(result, ['fallbackUsed', 'fallback_used']);
  const fallbackProvider = text(field(responseRecord, ['fallbackProvider', 'fallback_provider']))
    || text(field(result, ['fallbackProvider', 'fallback_provider']));
  const cached = field(responseRecord, ['_cached', 'cached']) ?? field(result, ['_cached', 'cached']);
  const stale = field(responseRecord, ['isStale', 'is_stale']) ?? field(result, ['isStale', 'is_stale']);
  const freshnessUnknown = field(responseRecord, ['freshnessUnknown', 'freshness_unknown'])
    ?? field(result, ['freshnessUnknown', 'freshness_unknown']);
  const barComplete = field(responseRecord, ['barComplete', 'bar_complete'])
    ?? field(result, ['barComplete', 'bar_complete']);
  const warnings = uniqueStrings([
    ...errorStringList(field(responseRecord, ['warnings', 'warning'])),
    ...errorStringList(field(result, ['warnings', 'warning'])),
  ]);
  const errors = uniqueStrings([
    ...errorStringList(field(responseRecord, ['errors', 'error'])),
    ...errorStringList(field(result, ['errors', 'error'])),
  ]);
  const attempts = sourceAttemptList(
    field(responseRecord, ['sourceAttempts', 'source_attempts'])
      ?? field(result, ['sourceAttempts', 'source_attempts']),
  );
  const refs = uniqueStrings([
    ...stringList(field(responseRecord, ['sourceRefs', 'source_refs'])),
    ...stringList(field(result, ['sourceRefs', 'source_refs'])),
  ]);
  const links = uniqueStrings([
    ...stringList(field(responseRecord, ['referenceLinks', 'reference_links'])),
    ...stringList(field(result, ['referenceLinks', 'reference_links'])),
    ...refs,
  ]).filter(isHttpUrl);
  const sampleRows = rows.length > 6
    ? [...rows.slice(0, 3), '… 中间数据省略 …', ...rows.slice(-3)]
    : rows;

  return <div className="mt-2 space-y-2 rounded-lg border border-cyan/20 bg-cyan/5 p-2.5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p className="text-[11px] font-semibold text-foreground">请求与返回审计</p>
        <p className="mt-0.5 text-[10px] text-secondary-text">
          {fullResponseLoaded ? '已加载执行账本中的实际请求与规范化返回。' : '当前先展示运行摘要；点击加载后可核对完整返回。'}
        </p>
      </div>
      {!payloadsLoaded && onLoadPayloads ? <button
        type="button"
        onClick={onLoadPayloads}
        disabled={payloadsLoading}
        className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-[10px] text-secondary-text transition hover:text-foreground disabled:opacity-60"
      >
        <LoaderCircle className={cn('size-3', payloadsLoading && 'animate-spin')} />
        {payloadsLoading ? '加载中…' : '重新加载原始记录'}
      </button> : null}
    </div>

    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
      <AuditField label="实际来源" value={source || '未标注'} />
      <AuditField label="来源标识" value={sourceKey || '未提供'} />
      <AuditField
        label="正文访问"
        value={accessMode === 'reference_only'
          ? '仅来源引用，未访问正文'
          : accessMode === 'content_read'
            ? `${contentRead === false ? '读取失败' : contentExtracted === true ? '已读取并提取' : contentRead === true ? '已读取但未提取正文' : '读取状态未记录'}${contentLength == null ? '' : ` · ${contentLength} 字`}${extractionMethod ? ` · ${extractionMethod}` : ''}`
            : '结构化数据，不适用正文读取'}
        tone={accessMode === 'reference_only' || contentRead === false || (accessMode === 'content_read' && contentExtracted !== true) ? 'warning' : accessMode === 'content_read' ? 'success' : 'neutral'}
      />
      <AuditField label="返回数量" value={count == null ? '未提供' : `${count} 条`} />
      <AuditField label="数据时间" value={dataTime ? `${displayDataTime(dataTime)}${dataTimeProvenance ? `（${dataTimeProvenance}）` : ''}` : '未提供'} />
      <AuditField label="请求完成" value={booleanLabel(success, '成功', '失败')} tone={success === true ? 'success' : success === false ? 'danger' : 'neutral'} />
      <AuditField label="数据状态" value={
        fallbackUsed === true
          ? `已降级${fallbackProvider ? `：${fallbackProvider}` : ''}`
          : stale === true
            ? '过期数据'
            : freshnessUnknown === true
              ? '新鲜度未知'
              : partial === true || barComplete === false
                ? '不完整'
                : '未发现异常标记'
      } tone={fallbackUsed === true || stale === true || freshnessUnknown === true || partial === true || barComplete === false ? 'warning' : 'neutral'} />
    </div>
    {sourceOrigin || sourceScope || cached === true ? <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-secondary-text">
      {sourceOrigin ? <span>原始来源：{sourceOrigin}</span> : null}
      {sourceScope ? <span>来源范围：{sourceScope}</span> : null}
      {cached === true ? <span>本次使用缓存</span> : null}
    </div> : null}

    {attempts.length > 0 ? <div className="rounded-md border border-border/70 bg-card/70 p-2">
      <p className="text-[10px] font-semibold text-foreground">来源尝试链路</p>
      <div className="mt-1 space-y-1">
        {attempts.map((attempt, index) => <div key={`${text(attempt.source) || text(attempt.label) || 'source'}-${index}`} className="flex flex-wrap items-start justify-between gap-2 text-[10px]">
          <span className="font-medium">{text(attempt.label) || text(attempt.source) || `来源 ${index + 1}`}</span>
          <span className={cn('text-right', text(attempt.status) === 'success' ? 'text-success' : 'text-danger')}>
            {text(attempt.status) === 'success' ? `成功${numberValue(attempt.count) == null ? '' : ` · ${numberValue(attempt.count)} 条`}` : text(attempt.error) || text(attempt.errorType) || text(attempt.error_type) || '未返回'}
          </span>
        </div>)}
      </div>
    </div> : null}

    {warnings.length > 0 ? <NoticeList title="返回警告" items={warnings} tone="warning" /> : null}
    {errors.length > 0 ? <NoticeList title="返回错误" items={errors} tone="danger" /> : null}
    <JsonBlock title="实际请求参数" value={request} empty="执行账本没有保存可展示的请求参数。" />
    {sampleRows.length > 0 ? <JsonBlock title={`返回数据样本（${rows.length} 条中展示 ${Math.min(rows.length, 6)} 条）`} value={sampleRows} /> : null}
    <JsonBlock title={fullResponseLoaded ? '规范化返回 JSON（已脱敏）' : '返回摘要 JSON'} value={rawResponse} empty="该工具没有返回可展示的响应体。" />
    {links.length > 0 ? <div className="rounded-md border border-border/70 bg-card/70 p-2">
      <p className="text-[10px] font-semibold text-foreground">{accessMode === 'reference_only' ? '来源引用（不代表已访问）' : accessMode === 'content_read' ? '已读取来源' : '来源链接'}</p>
      <div className="mt-1 space-y-0.5 border-l border-border pl-2">
        {links.map((link) => <a key={link} href={link} target="_blank" rel="noreferrer" className="block break-all text-[10px] text-cyan hover:underline">{link}</a>)}
      </div>
    </div> : refs.length > 0 ? <p className="text-[10px] text-secondary-text">来源引用：{refs.join('、')}</p> : null}
  </div>;
}

function booleanLabel(value: unknown, yes: string, no: string) {
  if (value === true || text(value).toLowerCase() === 'true') return yes;
  if (value === false || text(value).toLowerCase() === 'false') return no;
  return '未说明';
}

function AuditField({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}) {
  return <div className="rounded-md border border-border/70 bg-card/70 px-2 py-1.5">
    <p className="text-[10px] text-secondary-text">{label}</p>
    <p className={cn('mt-0.5 line-clamp-2 break-all text-[11px] font-medium', tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : tone === 'danger' ? 'text-danger' : 'text-foreground')} title={value}>{value}</p>
  </div>;
}

function NoticeList({ title, items, tone }: { title: string; items: string[]; tone: 'warning' | 'danger' }) {
  return <div className={cn('rounded-md border px-2 py-1.5', tone === 'danger' ? 'border-danger/25 bg-danger/5' : 'border-warning/25 bg-warning/5')}>
    <p className={cn('text-[10px] font-semibold', tone === 'danger' ? 'text-danger' : 'text-warning')}>{title}</p>
    <ul className="mt-1 space-y-0.5 pl-3 text-[10px] leading-4 text-foreground/80">
      {items.map((item, index) => <li key={`${item}-${index}`} className="list-disc break-words">{item}</li>)}
    </ul>
  </div>;
}

function JsonBlock({ title, value, empty = '没有可展示的数据。' }: { title: string; value: unknown; empty?: string }) {
  const [copied, setCopied] = useState(false);
  const hasContent = hasValue(value);
  const serialized = hasContent ? jsonString(value) : '';
  const copy = async () => {
    if (!serialized || typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(serialized);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return <div className="rounded-md border border-border/70 bg-card/70 p-2">
    <div className="flex items-center justify-between gap-2">
      <p className="text-[10px] font-semibold text-foreground">{title}</p>
      {serialized ? <button type="button" onClick={() => void copy()} className="inline-flex items-center gap-1 text-[10px] text-secondary-text transition hover:text-foreground" title="复制脱敏后的 JSON">
        <Copy className="size-3" />{copied ? '已复制' : '复制 JSON'}
      </button> : null}
    </div>
    {serialized ? <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/60 p-2 font-mono text-[10px] leading-4 text-foreground/80">{previewJson(value)}</pre> : <p className="mt-1.5 text-[10px] text-secondary-text">{empty}</p>}
  </div>;
}

export function ErrorDetails({
  title,
  errorCode,
  details,
  defaultOpen = false,
  fallback,
}: {
  title: string;
  errorCode: string;
  details: string[];
  defaultOpen?: boolean;
  fallback: string;
}) {
  const content = [errorCode ? `错误代码：${errorCode}` : '', ...details].filter(Boolean).join('\n') || fallback;
  return <details open={defaultOpen} className="mt-2 rounded-md border border-danger/25 bg-danger/5 px-2.5 py-2 text-danger">
    <summary className="cursor-pointer select-none text-[11px] font-medium hover:opacity-80">{title}</summary>
    <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-foreground/80">{content}</pre>
  </details>;
}

export function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="rounded-lg bg-muted/60 p-2.5">{icon}<p className="mt-1 text-[11px] text-secondary-text">{label}</p><p className="mt-0.5 text-sm font-medium">{value}</p></div>;
}

export function BehaviorAuditCard({
  audit,
  onSampleSources,
  sourceSampling = false,
  sourceSample,
}: {
  audit?: AgentBehaviorAudit;
  onSampleSources?: () => void;
  sourceSampling?: boolean;
  sourceSample?: AgentSourceSampleResponse | null;
}) {
  if (!audit) return null;
  const actionCount = audit.actionRequiredCount ?? audit.dangerCount + audit.warningCount;
  const advisoryCount = audit.advisoryCount ?? audit.infoCount ?? 0;
  const actionableFindings = audit.findings.filter((finding) => finding.disposition !== 'advisory');
  const advisoryFindings = audit.findings.filter((finding) => finding.disposition === 'advisory');
  const statusLabel = audit.status === 'danger'
    ? '存在需要处理的执行或数据问题'
    : audit.status === 'warning'
      ? '有需要处理的核对问题'
      : audit.status === 'info'
        ? '运行完成，有观察提示'
        : '自动核对通过';
  const statusTone = audit.status === 'danger' ? 'danger' : audit.status === 'warning' ? 'warning' : audit.status === 'info' ? 'info' : 'success';
  const statusIcon = audit.status === 'clear'
    ? <ShieldCheck className="size-4 text-success" />
    : audit.status === 'info'
      ? <Activity className="size-4 text-cyan" />
      : <TriangleAlert className={cn('size-4', audit.status === 'danger' ? 'text-danger' : 'text-warning')} />;
  return <div id="run-behavior-audit" className="scroll-mt-3">
      <Card padding="none" className="rounded-xl p-3" title="执行诊断" subtitle="根据调用记录核对执行、数据和证据异常，并保留恢复情况">
      <div className={cn('flex items-start justify-between gap-2 rounded-lg border px-3 py-2.5', audit.status === 'danger' ? 'border-danger/25 bg-danger/5' : audit.status === 'warning' ? 'border-warning/25 bg-warning/8' : audit.status === 'info' ? 'border-cyan/25 bg-cyan/5' : 'border-success/25 bg-success/8')}>
        <div className="flex min-w-0 items-start gap-2">
          {statusIcon}
          <div className="min-w-0">
            <p className={cn('text-xs font-semibold', audit.status === 'danger' ? 'text-danger' : audit.status === 'warning' ? 'text-warning' : audit.status === 'info' ? 'text-cyan' : 'text-success')}>{statusLabel}</p>
            <p className="mt-0.5 text-[11px] leading-5 text-foreground/75">
              {actionCount > 0
                ? `发现 ${actionCount} 个需要处理的核对项（高风险 ${audit.dangerCount}，待核对 ${Math.max(0, actionCount - audit.dangerCount)}）。`
                : `当前没有需要人工处理的异常${advisoryCount ? `，保留 ${advisoryCount} 条观察提示供排查。` : '。'} `}
              {actionCount > 0
                ? '请核对下方异常项及其对应调用。'
                : '自动核对未发现待处理异常，不代表回答中的事实已经全部验证。'}
            </p>
          </div>
        </div>
        <Badge variant={statusTone}>{audit.riskScore} 风险分</Badge>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        <Metric icon={<Activity className="size-3.5 text-cyan" />} label="模型轮次 / 工具调用" value={`${audit.modelTurnCount} / ${audit.toolCallCount}`} />
        <Metric icon={<Link2 className="size-3.5 text-purple" />} label="候选 / 未读取（未选不等于失败）" value={`${audit.referenceLinkCount} / ${audit.unreadReferenceCount}`} />
        <Metric icon={<Database className="size-3.5 text-emerald-600" />} label="正文读取 / 提取" value={`${audit.contentReadCallCount} / ${audit.contentExtractedCallCount}`} />
        <Metric icon={<CheckCircle2 className="size-3.5 text-success" />} label="证据 / 结论" value={`${audit.evidenceCount} / ${audit.claimCount}`} />
      </div>
      {actionableFindings.length > 0 ? <FindingList title={`需要处理（${actionableFindings.length}）`} findings={actionableFindings} /> : null}
      {advisoryFindings.length > 0 ? <FindingList title={`自动观察（${advisoryFindings.length}，不阻断本次运行）`} findings={advisoryFindings} advisory /> : null}
      <details className="mt-2 rounded-md border border-border/70 bg-card/60 px-2.5 py-2">
        <summary className="cursor-pointer select-none text-[10px] font-medium text-secondary-text hover:text-foreground">查看全部自动检查项</summary>
        <div className="mt-1.5 grid gap-1 sm:grid-cols-2">{audit.checks.map((check) => <div key={check.code} className="flex items-start gap-1.5 text-[10px]">
          <span className={cn('mt-0.5 size-1.5 shrink-0 rounded-full', check.status === 'danger' ? 'bg-danger' : check.status === 'warning' ? 'bg-warning' : check.status === 'info' ? 'bg-cyan' : 'bg-success')} />
          <span><span className="font-medium text-foreground">{check.label}</span><span className="ml-1 text-secondary-text">{check.detail}</span></span>
        </div>)}</div>
      </details>
      {audit.sampling?.available && onSampleSources ? <div className="mt-2 rounded-lg border border-cyan/20 bg-cyan/5 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold text-foreground">系统自动抽检</p>
            <p className="mt-0.5 text-[10px] text-secondary-text">抽检最多 {audit.sampling.sampleLimit} 条来源，只验证链接可访问和正文能否提取，不代表模型原分析时阅读过。</p>
          </div>
          <button type="button" onClick={onSampleSources} disabled={sourceSampling} className="inline-flex items-center gap-1 rounded-md border border-cyan/30 bg-card px-2 py-1 text-[10px] font-medium text-cyan transition hover:bg-cyan/8 disabled:opacity-60">
            {sourceSampling ? <LoaderCircle className="size-3 animate-spin" /> : <Link2 className="size-3" />}
            {sourceSampling ? '抽检中…' : '抽检来源'}
          </button>
        </div>
        {sourceSample ? <div className="mt-2 space-y-1 border-t border-cyan/15 pt-2">
          {sourceSample.items.map((item) => <div key={item.url} className="rounded-md bg-card/70 px-2 py-1.5 text-[10px]">
            <div className="flex items-start justify-between gap-2"><span className="min-w-0 break-all text-foreground"><span className="mr-1 text-secondary-text">{item.kind === 'document' ? 'PDF/文档' : '文章'}</span>{item.url}</span><span className={item.success ? 'shrink-0 text-success' : 'shrink-0 text-danger'}>{item.success ? '可读取' : '不可读取'}</span></div>
            <p className="mt-0.5 text-secondary-text">{item.contentLength ? `${item.contentLength} 字` : '无正文'}{item.extractionMethod ? ` · ${item.extractionMethod}` : ''}{item.errors?.length ? ` · ${item.errors[0]}` : ''}</p>
            {item.contentPreview ? <details className="mt-1"><summary className="cursor-pointer text-secondary-text">查看抽检正文预览</summary><p className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words text-foreground/75">{item.contentPreview}</p></details> : null}
          </div>)}
          <p className="text-[10px] text-secondary-text">{sourceSample.note}</p>
        </div> : null}
      </div> : null}
    </Card>
  </div>;
}

function FindingList({
  title,
  findings,
  advisory = false,
}: {
  title: string;
  findings: AgentBehaviorAudit['findings'];
  advisory?: boolean;
}) {
  return <div className="mt-2 space-y-1.5">
    <p className={cn('text-[11px] font-semibold', advisory ? 'text-cyan' : 'text-foreground')}>{title}</p>
    {findings.map((finding) => {
      const tone = advisory ? 'info' : finding.severity;
      return <div key={`${finding.code}-${finding.title}`} className={cn('rounded-lg border px-3 py-2', tone === 'danger' ? 'border-danger/20 bg-danger/5' : tone === 'info' ? 'border-cyan/20 bg-cyan/5' : 'border-warning/20 bg-warning/8')}>
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-start gap-2">
            {tone === 'info' ? <Activity className="mt-0.5 size-3.5 shrink-0 text-cyan" /> : <TriangleAlert className={cn('mt-0.5 size-3.5 shrink-0', tone === 'danger' ? 'text-danger' : 'text-warning')} />}
            <div className="min-w-0">
              <p className={cn('text-xs font-medium', tone === 'danger' ? 'text-danger' : tone === 'info' ? 'text-cyan' : 'text-warning')}>{finding.title}</p>
              <p className="mt-0.5 text-[11px] leading-5 text-foreground/75">{finding.detail}</p>
              {finding.remediation ? <p className="mt-1 text-[10px] leading-4 text-secondary-text">建议：{finding.remediation}</p> : null}
            </div>
          </div>
          <button type="button" onClick={() => document.getElementById('run-tools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })} className="shrink-0 rounded-md border border-border/70 bg-card px-2 py-1 text-[10px] text-secondary-text transition hover:text-foreground">查看调用链</button>
        </div>
        {finding.links && finding.links.length > 0 ? <details className="mt-1.5 pl-5 text-[10px] text-secondary-text">
          <summary className="cursor-pointer select-none hover:text-foreground">查看 {finding.links.length} 条关联来源（仅展示，不代表已读取）</summary>
          <div className="mt-1 space-y-0.5 border-l border-border pl-2">{finding.links.map((link) => <a key={link} href={link} target="_blank" rel="noreferrer" className="block break-all text-cyan hover:underline">{link}</a>)}</div>
        </details> : null}
      </div>;
    })}
  </div>;
}

export function ClaimEvidenceCard({ claims }: { claims: Array<Record<string, unknown>> }) {
  if (claims.length === 0) return null;
  const checkLabels: Record<string, string> = {
    referenceIntegrity: '引用有效性',
    reference_integrity: '引用有效性',
    tool_success: '工具成功',
    entity_scope: '主体',
    toolSuccess: '工具成功',
    source: '来源',
    entityScope: '主体',
    time: '时间',
  };
  return <Card padding="none" className="rounded-xl p-3" title="回答与证据核对" subtitle="自动检查结论是否能追溯到工具返回和来源">
    <div className="space-y-1.5">
      {claims.slice(0, 40).map((claim, index) => {
        const checks = record(claim.checks);
        const evidenceIds = stringList(claim.evidenceIds ?? claim.evidence_ids);
        const unresolvedIds = stringList(claim.unresolvedEvidenceIds ?? claim.unresolved_evidence_ids);
        const claimIssues = stringList(claim.issues);
        const requiresEvidence = (claim.requiresEvidence ?? claim.requires_evidence) !== false;
        const failed = Object.values(checks).some((value) => value !== true)
          || unresolvedIds.length > 0 || claimIssues.length > 0 || (requiresEvidence && evidenceIds.length === 0);
        return <div key={text(claim.claimId ?? claim.claim_id) || index} className={cn('rounded-lg border px-2.5 py-2', failed ? 'border-warning/20 bg-warning/8' : 'border-border/70 bg-card/60')}>
          <div className="flex items-start justify-between gap-2">
            <p className="min-w-0 flex-1 line-clamp-3 text-xs leading-5 text-foreground">{text(claim.text) || '未记录结论文本'}</p>
            <Badge variant={failed ? 'warning' : 'success'}>{evidenceIds.length > 0 ? `${evidenceIds.length} 条证据` : requiresEvidence ? '无证据' : '无需引证'}</Badge>
          </div>
          {unresolvedIds.length > 0 ? <p className="mt-1 break-all text-xs text-danger">无效引用：{unresolvedIds.join('、')}</p> : null}
          {claimIssues.map((issue) => <p key={issue} className="mt-1 text-xs text-danger">{issue}</p>)}
          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-secondary-text">
            {Object.entries(checks).map(([key, value]) => <span key={key} className={value === true ? 'text-success' : 'text-danger'}>{checkLabels[key] ?? key}：{value === true ? '通过' : '未通过'}</span>)}
            {evidenceIds.length > 0 ? <span className="font-mono">{evidenceIds.slice(0, 3).join('、')}</span> : null}
          </div>
        </div>;
      })}
    </div>
    {claims.length > 40 ? <p className="mt-1.5 text-[10px] text-secondary-text">其余 {claims.length - 40} 条结论已折叠。</p> : null}
  </Card>;
}

export function EvidenceRow({ item, index }: { item: Record<string, unknown>; index: number }) {
  const refs = stringList(item.sourceRefs);
  const sources = uniqueStrings(refs.map(sourceLabel));
  const urls = refs.filter(isHttpUrl);
  const evidenceId = text(item.evidenceId) || text(item.id) || `证据 ${index + 1}`;
  return <div className="px-2.5 py-2"><div className="flex items-center justify-between gap-2"><span className="line-clamp-2 text-xs font-medium text-foreground" title={refs.join('、')}>{sources.join('、') || text(item.toolName) || '未标注来源'}</span><span className="shrink-0 text-[10px] text-secondary-text">{displayDataTime(item.dataTime)}</span></div><p className="mt-0.5 truncate font-mono text-[10px] text-secondary-text" title={evidenceId}>{evidenceId}</p>{urls.length > 0 ? <details className="mt-1.5 text-[10px] text-secondary-text"><summary className="cursor-pointer select-none hover:text-foreground">查看 {urls.length} 条原始链接</summary><div className="mt-1 space-y-0.5 border-l border-border pl-2">{urls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer" className="block break-all text-cyan hover:underline">{url}</a>)}</div></details> : null}</div>;
}
