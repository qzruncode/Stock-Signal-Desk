import { useState } from "react";
import { AlertCircleIcon, CheckCircle2Icon, ExternalLinkIcon, FileTextIcon, Loader2Icon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import type { KnowledgeBaseDocument, KnowledgeBasePreview } from "../../api/knowledgeBase";
import { cn } from "../../utils/cn";

export const ACTIVE_TASK_STATUSES = new Set(['queued', 'processing']);
export const CHUNK_PAGE_SIZE = 20;
export const DOCUMENT_PAGE_SIZE = 20;

function statusLabel(status: string): string {
  return ({
    queued: '等待处理',
    processing: '正在解析/建索引',
    ready: '可检索',
    failed: '处理失败',
    unsupported: '识别未通过',
    deleting: '正在删除',
    deleted: '已删除',
  } as Record<string, string>)[status] ?? status;
}

function taskStageLabel(stage: string): string {
  return ({
    starting: '正在启动',
    parsing: '正在解析 PDF',
    embedding: '正在生成向量',
    indexing: '正在写入索引',
    verifying: '正在核验索引',
    retry_wait: '等待自动重试',
    worker_recovery: '正在恢复任务',
    removing_file: '正在清理文件',
    complete: '处理完成',
  } as Record<string, string>)[stage] ?? '等待后台处理';
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ErrorNotice({ message, onClose }: { message: string; onClose: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-2 border-l-2 border-red-500 bg-red-50/70 px-3 py-2 text-xs text-red-800">
      <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0 flex-1">{message}</span>
      <button type="button" onClick={onClose} className="shrink-0 text-[10px] underline">关闭</button>
    </div>
  );
}

export function DocumentRow({
  document,
  selected,
  onSelect,
  onRetry,
  onDelete,
}: {
  document: KnowledgeBaseDocument;
  selected: boolean;
  onSelect: () => void;
  onRetry: (rebuild: boolean) => void;
  onDelete: () => void;
}) {
  const statusTone = document.status === 'ready'
    ? 'bg-emerald-50 text-emerald-700'
    : document.status === 'failed' || document.status === 'unsupported'
      ? 'bg-amber-50 text-amber-800'
      : document.status === 'deleting'
        ? 'bg-muted text-muted-foreground'
        : 'bg-blue-50 text-blue-700';
  const failedDelete = document.status === 'deleting' && document.task?.status === 'failed';
  const busy = ACTIVE_TASK_STATUSES.has(document.task?.status ?? document.status)
    || (document.status === 'deleting' && !failedDelete);
  const errorDetail = document.task?.status === 'queued' || document.task?.status === 'processing'
    ? document.task.errorDetail
    : document.errorDetail;
  const label = document.task?.status === 'processing'
    ? taskStageLabel(document.task.stage)
    : statusLabel(document.status);

  return (
    <div className={cn('grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 border-t border-border/70 px-1 py-2 sm:grid-cols-[minmax(0,1fr)_5rem_8rem_7rem]', selected && 'bg-primary/[0.035]')}>
      <button type="button" onClick={onSelect} className="flex min-w-0 items-center gap-2 text-left">
        <FileTextIcon className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0">
          <span className="block truncate text-xs font-medium text-foreground">{document.filename}</span>
          <span className="mt-0.5 block text-[10px] text-muted-foreground">{formatBytes(document.sizeBytes)} · {document.chunkCount || 0} 块</span>
          {document.source ? <span className="mt-0.5 block truncate text-[10px] text-primary/80" title={`${document.source.exchange} · ${document.source.reportPeriod}`}>{document.source.exchange} · {document.source.reportPeriod}</span> : null}
        </span>
      </button>
      <span className="hidden text-xs text-muted-foreground sm:block">{document.pageCount || '—'}</span>
      <span className={cn('inline-flex w-fit items-center gap-1 rounded px-1.5 py-1 text-[10px] font-medium', statusTone)}>
        {busy ? <Loader2Icon className="size-3 animate-spin" /> : document.status === 'ready' ? <CheckCircle2Icon className="size-3" /> : null}
        {label}
        {busy && document.task ? <span>{document.task.progress}%</span> : null}
      </span>
      <div className="flex items-center justify-end gap-1 sm:flex">
        {document.status === 'failed' || (document.status === 'unsupported' && document.errorCode === 'extraction_quality_low') ? (
          <button
            type="button"
            onClick={() => onRetry(false)}
            title={document.status === 'unsupported' ? '重新识别' : '重试处理'}
            aria-label={`${document.status === 'unsupported' ? '重新识别' : '重试处理'} ${document.filename}`}
            className="inline-flex size-7 items-center justify-center rounded-md text-primary hover:bg-primary/5"
          ><RefreshCwIcon className="size-3.5" /></button>
        ) : null}
        {document.status === 'ready' ? (
          <button type="button" onClick={() => onRetry(true)} title="重建索引" className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"><RefreshCwIcon className="size-3.5" /></button>
        ) : null}
        {document.status !== 'deleting' || failedDelete ? (
          <button
            type="button"
            onClick={onDelete}
            title={failedDelete ? '重试清理' : '删除文档'}
            aria-label={failedDelete ? `重试清理 ${document.filename}` : `删除 ${document.filename}`}
            className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-red-50 hover:text-red-700"
          ><Trash2Icon className="size-3.5" /></button>
        ) : null}
      </div>
      {errorDetail ? <p className="col-span-full -mt-1 text-[10px] leading-4 text-amber-800 sm:col-start-1">{errorDetail}</p> : null}
    </div>
  );
}

export function DocumentPreview({
  document,
  preview,
  onLoadChunkPage,
  onLoadDocumentPage,
}: {
  document: KnowledgeBaseDocument;
  preview: KnowledgeBasePreview | null;
  onLoadChunkPage: (documentId: string, offset: number) => Promise<KnowledgeBasePreview['chunks'] | null>;
  onLoadDocumentPage: (documentId: string, offset: number) => Promise<KnowledgeBasePreview['pages'] | null>;
}) {
  const [activeView, setActiveView] = useState<'chunks' | 'pages'>('chunks');
  const [chunkOffset, setChunkOffset] = useState(0);
  const [chunkPage, setChunkPage] = useState<KnowledgeBasePreview['chunks'] | null>(null);
  const [isLoadingChunkPage, setIsLoadingChunkPage] = useState(false);
  const [documentPageOffset, setDocumentPageOffset] = useState(0);
  const [documentPage, setDocumentPage] = useState<KnowledgeBasePreview['pages'] | null>(null);
  const [isLoadingDocumentPage, setIsLoadingDocumentPage] = useState(false);
  const chunks = chunkPage ?? preview?.chunks.slice(0, CHUNK_PAGE_SIZE) ?? [];
  const pages = documentPage ?? preview?.pages ?? [];
  const totalPageCount = document.pageCount || preview?.pageCount || pages.length;
  const chunkPageCount = Math.ceil(document.chunkCount / CHUNK_PAGE_SIZE);
  const currentChunkPage = Math.floor(chunkOffset / CHUNK_PAGE_SIZE) + 1;
  const documentPageCount = Math.ceil(totalPageCount / DOCUMENT_PAGE_SIZE);
  const currentDocumentPage = Math.floor(documentPageOffset / DOCUMENT_PAGE_SIZE) + 1;

  const changeChunkPage = async (nextOffset: number) => {
    if (isLoadingChunkPage || nextOffset < 0 || nextOffset >= document.chunkCount) return;
    setIsLoadingChunkPage(true);
    try {
      const nextPage = nextOffset === 0 ? preview?.chunks ?? [] : await onLoadChunkPage(document.id, nextOffset);
      if (nextPage) {
        setChunkPage(nextOffset === 0 ? null : nextPage);
        setChunkOffset(nextOffset);
      }
    } finally {
      setIsLoadingChunkPage(false);
    }
  };

  const changeDocumentPage = async (nextOffset: number) => {
    if (isLoadingDocumentPage || nextOffset < 0 || nextOffset >= totalPageCount) return;
    setIsLoadingDocumentPage(true);
    try {
      const nextPage = nextOffset === 0 ? preview?.pages ?? [] : await onLoadDocumentPage(document.id, nextOffset);
      if (nextPage) {
        setDocumentPage(nextOffset === 0 ? null : nextPage);
        setDocumentPageOffset(nextOffset);
      }
    } finally {
      setIsLoadingDocumentPage(false);
    }
  };

  return (
    <section className="mt-4 border-t border-border pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold">文档详情</h3>
          <p className="mt-0.5 max-w-[18rem] truncate text-[10px] text-muted-foreground" title={document.filename}>{document.filename} · {document.pageCount} 页 · {document.chunkCount} 块</p>
          {document.source ? <p className="mt-0.5 max-w-[24rem] truncate text-[10px] text-muted-foreground" title={`${document.source.provider} · ${document.source.announcementTitle}`}>{document.source.exchange} · {document.source.reportPeriod} · {document.source.provider}</p> : null}
        </div>
        <div className="flex items-center gap-1">
          {document.source?.pdfUrl ? <a href={document.source.pdfUrl} target="_blank" rel="noopener noreferrer" className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] text-muted-foreground hover:bg-muted/50 hover:text-foreground">官网原件 <ExternalLinkIcon className="size-3" /></a> : null}
          {document.status === 'ready' ? (
            <a
              href={`/api/v1/knowledge-bases/documents/${encodeURIComponent(document.id)}/content#page=1`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] text-primary hover:bg-muted/50"
            >打开 PDF <ExternalLinkIcon className="size-3" /></a>
          ) : null}
        </div>
      </div>
      {document.status !== 'ready' ? (
        <p className="mt-2 text-xs text-muted-foreground">处理完成后可预览页面与分块。</p>
      ) : !preview ? (
        <div className="flex items-center gap-2 py-5 text-xs text-muted-foreground"><Loader2Icon className="size-3.5 animate-spin" />加载文档预览…</div>
      ) : (
        <div className="mt-2">
          <div role="tablist" aria-label="文档详情视图" className="flex gap-4 border-b border-border/70">
            <button type="button" role="tab" aria-selected={activeView === 'chunks'} onClick={() => setActiveView('chunks')} className={cn('border-b-2 px-0.5 py-2 text-[11px] transition', activeView === 'chunks' ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground hover:text-foreground')}>检索切片 <span className="ml-1 tabular-nums">{document.chunkCount}</span></button>
            <button type="button" role="tab" aria-selected={activeView === 'pages'} onClick={() => setActiveView('pages')} className={cn('border-b-2 px-0.5 py-2 text-[11px] transition', activeView === 'pages' ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground hover:text-foreground')}>原文页面 <span className="ml-1 tabular-nums">{totalPageCount}</span></button>
          </div>
          {activeView === 'chunks' ? (
            <div className="flex items-center justify-between gap-2 border-b border-border/50 py-2 text-[10px] text-muted-foreground">
              <span>第 {document.chunkCount ? chunkOffset + 1 : 0}–{Math.min(chunkOffset + chunks.length, document.chunkCount)} 条 / 共 {document.chunkCount} 条</span>
              <div className="flex items-center gap-1.5">
                <button type="button" onClick={() => void changeChunkPage(Math.max(0, chunkOffset - CHUNK_PAGE_SIZE))} disabled={chunkOffset === 0 || isLoadingChunkPage} className="rounded px-2 py-1 hover:bg-muted disabled:opacity-40">上一页</button>
                <span className="tabular-nums">{currentChunkPage} / {Math.max(1, chunkPageCount)}</span>
                <button type="button" onClick={() => void changeChunkPage(chunkOffset + CHUNK_PAGE_SIZE)} disabled={chunkOffset + CHUNK_PAGE_SIZE >= document.chunkCount || isLoadingChunkPage} className="rounded px-2 py-1 hover:bg-muted disabled:opacity-40">{isLoadingChunkPage ? '加载中…' : '下一页'}</button>
              </div>
            </div>
          ) : null}
          {activeView === 'pages' ? (
            <div className="flex items-center justify-between gap-2 border-b border-border/50 py-2 text-[10px] text-muted-foreground">
              <span>第 {totalPageCount ? documentPageOffset + 1 : 0}–{Math.min(documentPageOffset + pages.length, totalPageCount)} 页 / 共 {totalPageCount} 页</span>
              <div className="flex items-center gap-1.5">
                <button type="button" onClick={() => void changeDocumentPage(Math.max(0, documentPageOffset - DOCUMENT_PAGE_SIZE))} disabled={documentPageOffset === 0 || isLoadingDocumentPage} className="rounded px-2 py-1 hover:bg-muted disabled:opacity-40">上一页</button>
                <span className="tabular-nums">{currentDocumentPage} / {Math.max(1, documentPageCount)}</span>
                <button type="button" onClick={() => void changeDocumentPage(documentPageOffset + DOCUMENT_PAGE_SIZE)} disabled={documentPageOffset + DOCUMENT_PAGE_SIZE >= totalPageCount || isLoadingDocumentPage} className="rounded px-2 py-1 hover:bg-muted disabled:opacity-40">{isLoadingDocumentPage ? '加载中…' : '下一页'}</button>
              </div>
            </div>
          ) : null}
          <div role="tabpanel" aria-label={activeView === 'chunks' ? '检索切片详情' : '原文页面详情'} className="max-h-[55dvh] divide-y divide-border/60 overflow-y-auto sm:max-h-[34rem]">
            {activeView === 'chunks' ? chunks.map((chunk) => (
              <article key={chunk.id} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-[11px] font-medium">分块 {chunk.chunkIndex + 1}{chunk.section ? ` · ${chunk.section}` : ''}</span>
                  <a href={`/api/v1/knowledge-bases/documents/${encodeURIComponent(document.id)}/content#page=${chunk.pageStart}`} target="_blank" rel="noopener noreferrer" className="shrink-0 text-[10px] text-primary hover:underline">第 {chunk.pageStart}{chunk.pageEnd !== chunk.pageStart ? `–${chunk.pageEnd}` : ''} 页 <ExternalLinkIcon className="inline size-3" /></a>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-xs leading-5 text-foreground/85">{chunk.text}</p>
              </article>
            )) : pages.map((page) => (
              <article key={page.pageNumber} className="py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium">第 {page.pageNumber} 页</span>
                  <a href={`/api/v1/knowledge-bases/documents/${encodeURIComponent(document.id)}/content#page=${page.pageNumber}`} target="_blank" rel="noopener noreferrer" className="text-[10px] text-primary hover:underline">打开此页</a>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{page.text || '本页未提取到文本'}</p>
              </article>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
