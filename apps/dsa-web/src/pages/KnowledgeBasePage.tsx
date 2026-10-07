import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileTextIcon,
  InfoIcon,
  Loader2Icon,
  PlugZapIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  Trash2Icon,
  UploadCloudIcon,
} from 'lucide-react';
import { knowledgeBaseApi, type KnowledgeBaseDocument, type KnowledgeBaseItem, type KnowledgeBaseModelServiceCheck, type KnowledgeBasePreview, type KnowledgeBaseSearchResult } from '../api/knowledgeBase';
import { toApiErrorMessage } from '../api/error';
import { Drawer } from '../components/common/Drawer';
import { Modal } from '../components/common/Modal';
import { Tooltip } from '../components/common/Tooltip';
import { cn } from '../utils/cn';
import { ACTIVE_TASK_STATUSES, CHUNK_PAGE_SIZE, DOCUMENT_PAGE_SIZE, DocumentPreview, DocumentRow, ErrorNotice } from "./knowledgeBase/KnowledgeBasePageComponents";

export default function KnowledgeBasePage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseItem[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState<string | null>(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchKnowledgeBaseId, setSearchKnowledgeBaseId] = useState('');
  const [documents, setDocuments] = useState<KnowledgeBaseDocument[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [preview, setPreview] = useState<KnowledgeBasePreview | null>(null);
  const [searchResults, setSearchResults] = useState<KnowledgeBaseSearchResult[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [isCheckingModels, setIsCheckingModels] = useState(false);
  const [modelServiceCheck, setModelServiceCheck] = useState<KnowledgeBaseModelServiceCheck | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadingName, setUploadingName] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const selectedKnowledgeBase = knowledgeBases.find((item) => item.id === selectedKnowledgeBaseId) ?? null;
  const searchKnowledgeBase = knowledgeBases.find((item) => item.id === searchKnowledgeBaseId) ?? null;
  const selectedDocument = documents.find((item) => item.id === selectedDocumentId) ?? null;

  const refreshKnowledgeBases = useCallback(async () => {
    const items = await knowledgeBaseApi.list();
    setKnowledgeBases(items.filter((item) => item.status !== 'deleted'));
    setSelectedKnowledgeBaseId((current) => (
      current && items.some((item) => item.id === current && item.status !== 'deleted')
        ? current
        : items.find((item) => item.status === 'active')?.id ?? null
    ));
    return items;
  }, []);

  const refreshDocuments = useCallback(async (knowledgeBaseId: string) => {
    const items = await knowledgeBaseApi.listDocuments(knowledgeBaseId);
    setDocuments(items);
    setKnowledgeBases((current) => current.map((knowledgeBase) => (
      knowledgeBase.id === knowledgeBaseId
        ? {
          ...knowledgeBase,
          documentCount: items.length,
          readyDocumentCount: items.filter((document) => document.status === 'ready').length,
        }
        : knowledgeBase
    )));
    setSelectedDocumentId((current) => (
      current && items.some((item) => item.id === current)
        ? current
        : null
    ));
    return items;
  }, []);

  useEffect(() => {
    let mounted = true;
    setIsLoading(true);
    void knowledgeBaseApi.list()
      .then((items) => {
        if (!mounted) return;
        const activeItems = items.filter((item) => item.status !== 'deleted');
        setKnowledgeBases(activeItems);
        setSelectedKnowledgeBaseId((current) => (
          current && activeItems.some((item) => item.id === current)
            ? current
            : activeItems.find((item) => item.status === 'active')?.id ?? null
        ));
      })
      .catch((requestError: unknown) => {
        if (mounted) setError(toApiErrorMessage(requestError, '知识库列表加载失败，请检查服务状态后重试。'));
      })
      .finally(() => {
        if (mounted) setIsLoading(false);
      });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    setSelectedDocumentId(null);
    setPreview(null);
    setSearchResults([]);
    if (!selectedKnowledgeBaseId) {
      setDocuments([]);
      return undefined;
    }
    let mounted = true;
    const load = async () => {
      try {
        await refreshDocuments(selectedKnowledgeBaseId);
      } catch (requestError) {
        if (mounted) setError(toApiErrorMessage(requestError, '文档列表加载失败。'));
      }
    };
    void load();
    const poll = window.setInterval(() => { void load(); }, 3_000);
    return () => {
      mounted = false;
      window.clearInterval(poll);
    };
  }, [selectedKnowledgeBaseId, refreshDocuments]);

  useEffect(() => {
    if (!selectedKnowledgeBaseId || !selectedDocumentId) {
      setPreview(null);
      return undefined;
    }
    let mounted = true;
    setPreview(null);
    void knowledgeBaseApi.preview(selectedDocumentId, undefined, 0, DOCUMENT_PAGE_SIZE)
      .then((value) => { if (mounted) setPreview(value); })
      .catch((requestError: unknown) => {
        if (mounted) setError(toApiErrorMessage(requestError, '文档结构预览加载失败。'));
      });
    return () => { mounted = false; };
  }, [selectedKnowledgeBaseId, selectedDocumentId]);

  const isProcessing = useMemo(
    () => documents.some((document) => ACTIVE_TASK_STATUSES.has(document.status) || document.status === 'deleting'),
    [documents],
  );

  const handleCreateKnowledgeBase = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newName.trim()) return;
    setIsCreating(true);
    setError(null);
    try {
      const created = await knowledgeBaseApi.create({ name: newName.trim(), description: newDescription.trim() });
      await refreshKnowledgeBases();
      setSelectedKnowledgeBaseId(created.id);
      setNewName('');
      setNewDescription('');
      setIsCreateDialogOpen(false);
      setIsDetailOpen(true);
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '创建知识库失败。'));
    } finally {
      setIsCreating(false);
    }
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files?.length || !selectedKnowledgeBaseId) return;
    const pdfFiles = Array.from(files).filter((file) => file.name.toLowerCase().endsWith('.pdf'));
    if (!pdfFiles.length) {
      setError('请选择 PDF 文件。');
      return;
    }
    const tooLarge = pdfFiles.find((file) => file.size > 50 * 1024 * 1024);
    if (tooLarge) {
      setError(`“${tooLarge.name}”超过 50 MB 上传上限。`);
      return;
    }
    setIsUploading(true);
    setError(null);
    try {
      for (const file of pdfFiles) {
        setUploadingName(file.name);
        await knowledgeBaseApi.upload(selectedKnowledgeBaseId, file);
        await refreshDocuments(selectedKnowledgeBaseId);
      }
      await refreshKnowledgeBases();
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '上传失败，请确认文件是有效 PDF 后重试。'));
    } finally {
      setIsUploading(false);
      setUploadingName('');
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleRetry = async (document: KnowledgeBaseDocument, rebuildIndex: boolean) => {
    setError(null);
    try {
      await knowledgeBaseApi.retry(document.id, rebuildIndex);
      if (selectedKnowledgeBaseId) await refreshDocuments(selectedKnowledgeBaseId);
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '无法提交文档处理任务。'));
    }
  };

  const handleDeleteDocument = async (document: KnowledgeBaseDocument) => {
    if (!window.confirm(`确定删除“${document.filename}”及其 PDF 原件和索引吗？`)) return;
    setError(null);
    try {
      await knowledgeBaseApi.deleteDocument(document.id);
      setSelectedDocumentId(null);
      if (selectedKnowledgeBaseId) await refreshDocuments(selectedKnowledgeBaseId);
      await refreshKnowledgeBases();
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '删除文档失败。'));
    }
  };

  const handleDeleteKnowledgeBase = async () => {
    if (!selectedKnowledgeBase) return;
    if (!window.confirm(`确定删除知识库“${selectedKnowledgeBase.name}”以及其中全部文档吗？后台会清理原件和向量索引。`)) return;
    setError(null);
    try {
      await knowledgeBaseApi.delete(selectedKnowledgeBase.id);
      setSelectedDocumentId(null);
      setIsDetailOpen(false);
      await refreshKnowledgeBases();
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '删除知识库失败。'));
    }
  };

  const handleSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!searchKnowledgeBaseId || !searchQuery.trim()) return;
    setIsSearching(true);
    setError(null);
    setSearchResults([]);
    try {
      const result = await knowledgeBaseApi.search({
        query: searchQuery.trim(),
        knowledgeBaseIds: [searchKnowledgeBaseId],
        topK: 5,
      });
      setSearchResults(result.results ?? []);
      if (result.noEvidence) setError(result.message || '没有检索到相关内容。');
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '检索试验失败，请检查模型服务和 Qdrant 状态。'));
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  };

  const handleCheckModelServices = async () => {
    setIsCheckingModels(true);
    setError(null);
    try {
      setModelServiceCheck(await knowledgeBaseApi.testModelServices());
    } catch (requestError) {
      setError(toApiErrorMessage(requestError, '模型服务检查失败，请确认本机 Embedding 与 Reranker 服务已启动。'));
      setModelServiceCheck(null);
    } finally {
      setIsCheckingModels(false);
    }
  };

  const canUpload = Boolean(selectedKnowledgeBase && selectedKnowledgeBase.status === 'active' && !isUploading);

  const openSearch = (knowledgeBaseId: string) => {
    setSearchKnowledgeBaseId(knowledgeBaseId);
    setSearchResults([]);
    setSearchQuery('');
    setError(null);
    setIsSearchOpen(true);
  };

  const openKnowledgeBase = (knowledgeBaseId: string) => {
    setSelectedKnowledgeBaseId(knowledgeBaseId);
    setSelectedDocumentId(null);
    setError(null);
    setIsDetailOpen(true);
  };

  return (
      <div className="mx-auto flex h-full max-w-7xl flex-col gap-4 py-4 sm:gap-5 sm:py-5">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border/70 pb-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-tight">PDF 知识库</h1>
        </div>
        <div className="flex w-full items-center justify-between gap-3 sm:w-auto sm:flex-1">
          <button type="button" onClick={() => { setError(null); setIsCreateDialogOpen(true); }} className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-3.5 text-xs font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90"><PlusIcon className="size-4" />新建知识库</button>
          <div className="ml-auto flex items-center gap-1">
            <button type="button" onClick={() => void handleCheckModelServices()} disabled={isCheckingModels} className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted/50 hover:text-foreground disabled:opacity-50" title="模型服务" aria-label={isCheckingModels ? '正在检查模型服务' : '检查模型服务'}>{isCheckingModels ? <Loader2Icon className="size-4 animate-spin" /> : <PlugZapIcon className="size-4" />}</button>
            <button type="button" onClick={() => { void refreshKnowledgeBases(); if (selectedKnowledgeBaseId) void refreshDocuments(selectedKnowledgeBaseId); }} className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted/50 hover:text-foreground" aria-label="刷新知识库状态" title="刷新"><RefreshCwIcon className="size-4" /></button>
          </div>
        </div>
      </header>

      {error && !isDetailOpen && !isSearchOpen && !isCreateDialogOpen ? <ErrorNotice message={error} onClose={() => setError(null)} /> : null}

      {modelServiceCheck ? (
        <details className="group border-b border-border/70 pb-3 text-xs" open={!modelServiceCheck.success}>
          <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
            {modelServiceCheck.success ? <CheckCircle2Icon className="size-4 text-emerald-600" /> : <AlertCircleIcon className="size-4 text-amber-600" />}
            {modelServiceCheck.success ? '模型服务正常' : '模型服务检查未通过'}
            <span className="text-[10px] font-normal text-muted-foreground group-open:hidden">查看详情</span>
          </summary>
          <div className="mt-2 divide-y divide-border/60 sm:grid sm:grid-cols-2 sm:divide-y-0 sm:gap-x-6">
            {([
              ['Embedding', modelServiceCheck.embedding],
              ['Reranker', modelServiceCheck.reranker],
            ] as const).map(([label, result]) => (
              <div key={label} className="min-w-0 py-2">
                <p className="truncate font-medium" title={result.model}>{label} <span className="font-normal text-muted-foreground">{result.model.split('@')[0]} · {result.dimension ? `${result.dimension} 维 · ` : ''}{result.latencyMs == null ? '—' : `${result.latencyMs} ms`}</span></p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{result.message}</p>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <section className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between gap-3 pb-2.5 pt-1">
          <h2 className="text-sm font-semibold">全部知识库</h2>
          <span className="text-[11px] text-muted-foreground">{knowledgeBases.length} 个</span>
        </div>
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2Icon className="size-4 animate-spin" />正在加载知识库</div>
        ) : knowledgeBases.length ? (
          <div className="min-h-0 flex-1 overflow-y-auto border-t border-border/70">
            {knowledgeBases.map((item) => (
              <article key={item.id} className="group flex items-center gap-3 border-b border-border/60 px-2 py-3.5 transition hover:bg-muted/25 sm:gap-4 sm:px-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/[0.07] text-primary transition group-hover:bg-primary/10"><DatabaseIcon className="size-4" /></span>
                <button type="button" onClick={() => openKnowledgeBase(item.id)} className="min-w-0 flex-1 text-left">
                  <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="truncate text-sm font-medium text-foreground">{item.name}</span>
                    {item.description ? (
                      <Tooltip focusable ariaLabel={`${item.name}说明`} content={item.description} contentClassName="min-w-0 whitespace-normal">
                        <InfoIcon className="size-3 shrink-0 cursor-help text-muted-foreground transition-colors hover:text-primary" aria-hidden="true" />
                      </Tooltip>
                    ) : null}
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground"><span className={cn('size-1.5 rounded-full', item.readyDocumentCount ? 'bg-emerald-500' : 'bg-muted-foreground/40')} />{item.readyDocumentCount ? '可检索' : '暂无可检索文档'}</span>
                  </span>
                  <span className="mt-1 block truncate text-[11px] text-muted-foreground">{item.documentCount} 份文档 · {item.readyDocumentCount} 份可检索</span>
                </button>
                <div className="hidden shrink-0 text-right sm:block">
                  <p className="text-xs font-medium tabular-nums">{item.readyDocumentCount}<span className="mx-1 text-muted-foreground">/</span>{item.documentCount}</p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">可检索文档</p>
                </div>
                <button
                  type="button"
                  onClick={() => openSearch(item.id)}
                  disabled={!item.readyDocumentCount}
                  title={item.readyDocumentCount ? `在“${item.name}”中检索测试` : '该知识库暂无可检索文档'}
                  aria-label={`检索测试：${item.name}`}
                  className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted-foreground transition hover:bg-muted/60 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <SearchIcon className="size-3.5" />检索测试
                </button>
                <button type="button" onClick={() => openKnowledgeBase(item.id)} className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-primary transition hover:bg-primary/5">管理<ChevronRightIcon className="size-3.5" /></button>
              </article>
            ))}
          </div>
        ) : (
          <div className="flex min-h-64 flex-1 flex-col items-center justify-center border-t border-border/70 px-4 text-center">
            <span className="flex size-12 items-center justify-center rounded-2xl bg-primary/[0.07] text-primary"><DatabaseIcon className="size-5" /></span>
            <h3 className="mt-3 text-sm font-semibold">还没有知识库</h3>
            <p className="mt-1 text-xs text-muted-foreground">新建知识库后即可上传 PDF。</p>
            <button type="button" onClick={() => setIsCreateDialogOpen(true)} className="mt-4 inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"><PlusIcon className="size-3.5" />新建知识库</button>
          </div>
        )}
      </section>

      <Modal
        isOpen={isCreateDialogOpen}
        onClose={() => setIsCreateDialogOpen(false)}
        title="新建知识库"
        width="max-w-md"
        className="p-4 sm:p-5"
        footer={(
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setIsCreateDialogOpen(false)} className="inline-flex h-9 items-center rounded-lg px-3 text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground">取消</button>
            <button type="submit" form="knowledge-base-create-form" disabled={!newName.trim() || isCreating} className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-3.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">{isCreating ? <Loader2Icon className="size-3.5 animate-spin" /> : <PlusIcon className="size-3.5" />}创建</button>
          </div>
        )}
      >
        {error ? <ErrorNotice message={error} onClose={() => setError(null)} /> : null}
        <form id="knowledge-base-create-form" onSubmit={handleCreateKnowledgeBase} className="space-y-3">
          <div>
            <label htmlFor="kb-name" className="mb-1.5 block text-xs font-medium">名称</label>
            <input id="kb-name" autoFocus value={newName} onChange={(event) => setNewName(event.target.value)} maxLength={160} placeholder="例如：产品与年报" className="h-10 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none transition focus:border-primary/50 focus:ring-2 focus:ring-primary/10" />
          </div>
          <div>
            <label htmlFor="kb-description" className="mb-1.5 block text-xs font-medium">描述 <span className="font-normal text-muted-foreground">（可选）</span></label>
            <textarea id="kb-description" value={newDescription} onChange={(event) => setNewDescription(event.target.value)} maxLength={2_000} rows={3} placeholder="为知识库添加简短说明" className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary/50 focus:ring-2 focus:ring-primary/10" />
          </div>
        </form>
      </Modal>

      <Drawer isOpen={isDetailOpen && Boolean(selectedKnowledgeBase)} onClose={() => setIsDetailOpen(false)} title={selectedKnowledgeBase?.name ?? '知识库详情'} eyebrow={null} width="max-w-4xl">
        {selectedKnowledgeBase ? (
          <div className="space-y-4">
            {error ? <ErrorNotice message={error} onClose={() => setError(null)} /> : null}
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                <span>{documents.length} 份文档</span>
                <span>{documents.filter((document) => document.status === 'ready').length} 份可检索</span>
                <span className="inline-flex items-center gap-1">{isProcessing ? <Loader2Icon className="size-3 animate-spin text-primary" /> : <CheckCircle2Icon className="size-3 text-emerald-600" />}{isProcessing ? '索引处理中' : '索引已同步'}</span>
              </div>
              <button type="button" onClick={() => void handleDeleteKnowledgeBase()} className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-[11px] text-muted-foreground transition hover:bg-red-50 hover:text-red-700"><Trash2Icon className="size-3.5" />删除知识库</button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" multiple className="sr-only" aria-label="选择要上传的 PDF 文件" onChange={(event) => void handleUpload(event.target.files)} />
              <button type="button" disabled={!canUpload} onClick={() => fileInputRef.current?.click()} className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-3.5 text-xs font-medium text-primary-foreground shadow-sm transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50">{isUploading ? <Loader2Icon className="size-4 animate-spin" /> : <UploadCloudIcon className="size-4" />}{isUploading ? `正在上传 ${uploadingName}` : '上传 PDF'}</button>
              <span className="text-[11px] text-muted-foreground">≤50 MB · ≤500 页</span>
              <Tooltip content="支持文本型及扫描版 PDF（简体中文 OCR）；图表含义不会自动解读。"><InfoIcon className="size-3.5 text-muted-foreground" /></Tooltip>
            </div>

            <div>
              <div className="flex items-center justify-between gap-3 py-2">
                <div className="flex items-center gap-1.5">
                  <h3 className="text-xs font-semibold">文档</h3>
                  {documents.length ? (
                    <Tooltip focusable ariaLabel="文档详情说明" content="点击文件名可查看 PDF 页面原文和 RAG 检索切片。">
                      <InfoIcon className="size-3.5 cursor-help text-muted-foreground transition-colors hover:text-primary" aria-hidden="true" />
                    </Tooltip>
                  ) : null}
                </div>
              </div>
              {documents.length ? (
                <div className="divide-y divide-border/70 border-y border-border/70">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2 px-1 py-1.5 text-[10px] font-medium text-muted-foreground sm:grid-cols-[minmax(0,1fr)_5rem_8rem_7rem]"><span>文件</span><span className="hidden sm:block">页数</span><span>状态</span><span>操作</span></div>
                  {documents.map((document) => (
                    <DocumentRow key={document.id} document={document} selected={selectedDocumentId === document.id} onSelect={() => setSelectedDocumentId((current) => current === document.id ? null : document.id)} onRetry={(rebuild) => void handleRetry(document, rebuild)} onDelete={() => void handleDeleteDocument(document)} />
                  ))}
                </div>
              ) : (
                <div className="flex min-h-36 flex-col items-center justify-center border-y border-dashed border-border/70 text-center">
                  <FileTextIcon className="size-5 text-muted-foreground/70" />
                  <p className="mt-2 text-xs font-medium">暂无文档</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">上传 PDF 开始构建索引</p>
                </div>
              )}
            </div>

            {selectedDocument ? <DocumentPreview
              key={selectedDocument.id}
              document={selectedDocument}
              preview={preview}
              onLoadChunkPage={async (documentId, offset) => {
                try {
                  return (await knowledgeBaseApi.preview(documentId, undefined, offset, CHUNK_PAGE_SIZE)).chunks;
                } catch (requestError) {
                  setError(toApiErrorMessage(requestError, '切片详情加载失败。'));
                  return null;
                }
              }}
              onLoadDocumentPage={async (documentId, offset) => {
                try {
                  return (await knowledgeBaseApi.preview(documentId, undefined, offset, DOCUMENT_PAGE_SIZE)).pages;
                } catch (requestError) {
                  setError(toApiErrorMessage(requestError, '原文页面加载失败。'));
                  return null;
                }
              }}
            /> : null}
          </div>
        ) : null}
      </Drawer>

      <Drawer isOpen={isSearchOpen} onClose={() => setIsSearchOpen(false)} title="检索测试" eyebrow={searchKnowledgeBase ? `知识库 · ${searchKnowledgeBase.name}` : null} width="max-w-3xl">
        <div className="space-y-4">
          {error ? <ErrorNotice message={error} onClose={() => setError(null)} /> : null}
          <form onSubmit={(event) => void handleSearch(event)} className="flex gap-2">
            <input value={searchQuery} onChange={(event) => { setSearchQuery(event.target.value); setSearchResults([]); setError(null); }} maxLength={2_000} placeholder="输入问题或关键词" className="h-10 min-w-0 flex-1 rounded-lg border border-border bg-background px-3 text-sm outline-none transition focus:border-primary/50 focus:ring-2 focus:ring-primary/10" />
            <button type="submit" disabled={!searchKnowledgeBaseId || !searchQuery.trim() || isSearching || !knowledgeBases.some((item) => item.id === searchKnowledgeBaseId && item.readyDocumentCount > 0)} className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-xs font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">{isSearching ? <Loader2Icon className="size-3.5 animate-spin" /> : <SearchIcon className="size-3.5" />}检索</button>
          </form>
          {searchResults.length ? (
            <div className="divide-y divide-border/70 border-t border-border/70">
              {searchResults.map((result, index) => (
                <article key={result.citationId} className="py-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/[0.07] text-[10px] font-semibold text-primary">{index + 1}</span>
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium">{result.filename}</p>
                        <p className="mt-0.5 text-[10px] text-muted-foreground">第 {result.pageStart}{result.pageEnd !== result.pageStart ? `–${result.pageEnd}` : ''} 页{result.section ? ` · ${result.section}` : ''}</p>
                      </div>
                    </div>
                    <a href={result.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline">打开 PDF <ExternalLinkIcon className="size-3" /></a>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap pl-8 text-xs leading-5 text-foreground/90">{result.text || result.snippet}</p>
                </article>
              ))}
            </div>
          ) : isSearching ? (
            <div role="status" className="flex min-h-56 flex-col items-center justify-center border-t border-border/70 text-center text-muted-foreground">
              <Loader2Icon className="size-5 animate-spin text-primary" />
              <p className="mt-3 text-xs font-medium">正在检索当前知识库…</p>
            </div>
          ) : !error ? (
            <div className="flex min-h-56 flex-col items-center justify-center border-t border-border/70 text-center">
              <span className="flex size-11 items-center justify-center rounded-xl bg-primary/[0.07] text-primary"><SearchIcon className="size-5" /></span>
              <p className="mt-3 text-xs font-medium">输入问题开始测试</p>
              <p className="mt-1 text-[11px] text-muted-foreground">结果显示命中文档、页码与切片内容</p>
            </div>
          ) : null}
        </div>
      </Drawer>
    </div>
  );
}
