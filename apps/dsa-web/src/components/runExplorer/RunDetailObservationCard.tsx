import { useState } from "react";
import { Copy, LoaderCircle } from "lucide-react";
import { cn } from "../../utils/cn";
import { accessModeFor, arrayFrom, displayDataTime, errorStringList, field, hasValue, isHttpUrl, jsonString, numberValue, previewJson, record, sourceAttemptList, stringList, text, uniqueStrings } from "./RunDetailUtils";
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
