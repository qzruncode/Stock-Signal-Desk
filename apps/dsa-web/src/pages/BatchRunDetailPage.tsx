import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Bell, FileText, FolderPlus, Loader2, RefreshCw, Search, X } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { batchApi, type BatchRunItem } from '../api/batch';
import { ApiErrorAlert, Button, EmptyState } from '../components/common';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { cn } from '../utils/cn';
import { upsertWatchlistGroup } from '../utils/watchlistGroups';

interface BatchResultItem {
  code: string;
  success: boolean;
  model: string;
  text: string;
  summary: string;
  decision?: string;
}

function summarizeResult(text: string): string {
  const compact = text
    .split('\n')
    .map((line) => line.replace(/^#+\s*/, '').trim())
    .find((line) => line.length > 0) || '无摘要';
  return compact.length > 88 ? `${compact.slice(0, 87)}...` : compact;
}

function parseBatchResults(raw: string | null | undefined): BatchResultItem[] {
  if (!raw || raw === '[]' || raw === '{}') return [];
  try {
    const parsed = JSON.parse(raw) as Record<string, { success?: boolean; model?: string; text?: string; decision?: string }>;
    return Object.entries(parsed)
      .filter(([code, result]) => code !== '__all__' && result && typeof result === 'object')
      .map(([code, result]) => {
        const text = result.text || '';
        return {
          code,
          success: Boolean(result.success),
          model: result.model || '-',
          text,
          summary: summarizeResult(text),
          decision: result.decision,
        };
      });
  } catch {
    return [];
  }
}

function extractPassedCodesFromSummary(summaryMd: string): string[] {
  const lines = summaryMd.split('\n');
  const start = lines.findIndex((line) => /^##+\s+筛选通过股票/.test(line.trim()));
  if (start < 0) return [];
  const codes: string[] = [];
  for (const line of lines.slice(start + 1)) {
    const trimmed = line.trim();
    if (/^##+\s+/.test(trimmed)) break;
    if (!trimmed.startsWith('|') || trimmed.includes('---')) continue;
    const cells = trimmed.split('|').map((cell) => cell.trim()).filter(Boolean);
    const code = cells[0];
    if (/^[A-Za-z0-9.]+$/.test(code) && code !== '股票') {
      codes.push(code);
    }
  }
  return Array.from(new Set(codes));
}

const BatchRunDetailPage: React.FC = () => {
  const navigate = useNavigate();
  const { runId = '' } = useParams();
  const [run, setRun] = useState<BatchRunItem | null>(null);
  const [summaryMd, setSummaryMd] = useState('');
  const [activeView, setActiveView] = useState<'summary' | 'details'>('summary');
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [isNotifying, setIsNotifying] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [groupName, setGroupName] = useState('');
  const [resultSearch, setResultSearch] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const loadRun = useCallback(async () => {
    if (!runId) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setIsLoading(true);
    setError(null);
    setActionMessage(null);
    try {
      const [data, report] = await Promise.all([
        batchApi.getRunDetail(runId, ctrl.signal),
        batchApi.getRunReport(runId, ctrl.signal).catch(() => ''),
      ]);
      setRun(data);
      setSummaryMd(report);
      const first = parseBatchResults(data.results_json)[0];
      setSelectedCode((current) => current || first?.code || null);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    document.title = '跑批详情 - Stock-Signal-Desk';
    void loadRun();
    return () => { abortRef.current?.abort(); };
  }, [loadRun]);

  const handleRegenerateReport = useCallback(async () => {
    if (!runId) return;
    setIsRegenerating(true);
    setError(null);
    setActionMessage(null);
    try {
      const result = await batchApi.regenerateRunReport(runId);
      const [data, report] = await Promise.all([
        batchApi.getRunDetail(runId),
        batchApi.getRunReport(runId),
      ]);
      setRun(data);
      setSummaryMd(report);
      setActiveView('summary');
      setActionMessage(result.message);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsRegenerating(false);
    }
  }, [runId]);

  const handleNotifyRun = useCallback(async () => {
    if (!runId) return;
    setIsNotifying(true);
    setError(null);
    setActionMessage(null);
    try {
      const result = await batchApi.notifyRun(runId);
      const [data, report] = await Promise.all([
        batchApi.getRunDetail(runId),
        batchApi.getRunReport(runId),
      ]);
      setRun(data);
      setSummaryMd(report);
      setActiveView('summary');
      setActionMessage(result.message);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsNotifying(false);
    }
  }, [runId]);

  const results = useMemo(() => parseBatchResults(run?.results_json), [run]);
  const filteredResults = useMemo(() => {
    const keyword = resultSearch.trim().toLowerCase();
    if (!keyword) return results;
    return results.filter((item) => {
      const statusText = item.success ? '成功' : '失败';
      return [
        item.code,
        item.summary,
        item.model,
        item.decision || '',
        statusText,
      ].some((value) => value.toLowerCase().includes(keyword));
    });
  }, [resultSearch, results]);
  const passedCodes = useMemo(() => {
    const fromSummary = extractPassedCodesFromSummary(summaryMd);
    if (fromSummary.length > 0) return fromSummary;
    return results.filter((item) => item.decision === 'buy').map((item) => item.code);
  }, [results, summaryMd]);
  const selectedResult = useMemo(
    () => selectedCode ? results.find((item) => item.code === selectedCode) || null : null,
    [results, selectedCode],
  );
  const failedCount = results.filter((item) => !item.success).length;
  const successCount = results.length - failedCount;
  const successRate = run?.stock_count ? ((run.success_count / run.stock_count) * 100).toFixed(1) : '0.0';
  const defaultGroupName = useMemo(() => {
    const started = run?.started_at ? run.started_at.slice(0, 10) : new Date().toISOString().slice(0, 10);
    return `${run?.template_name || '跑批'}筛选-${started}`;
  }, [run]);

  useEffect(() => {
    if (activeView !== 'details') return;
    if (filteredResults.length === 0) {
      setSelectedCode(null);
      return;
    }
    if (!selectedCode || !filteredResults.some((item) => item.code === selectedCode)) {
      setSelectedCode(filteredResults[0].code);
    }
  }, [activeView, filteredResults, selectedCode]);

  const [isCreatingGroup, setIsCreatingGroup] = useState(false);
  const handleCreatePassedGroup = useCallback(async () => {
    if (passedCodes.length === 0 || isCreatingGroup) return;
    const targetName = groupName.trim() || defaultGroupName;
    setIsCreatingGroup(true);
    setError(null);
    setActionMessage(null);
    try {
      const group = await upsertWatchlistGroup(targetName, passedCodes, 'batch');
      setGroupName(group.name);
      setActionMessage(`已创建股票池分组「${group.name}」，共 ${group.codes.length} 只。`);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsCreatingGroup(false);
    }
  }, [defaultGroupName, groupName, isCreatingGroup, passedCodes]);

  return (
    <div className="flex h-[calc(100vh-1.5rem)] min-h-0 flex-col bg-base sm:h-[calc(100vh-2rem)]">
      <div className="border-b border-[#dbe3ed] bg-[#fbfcfe]/90 px-4 py-3 backdrop-blur-xl">
        <div className="flex w-full flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => navigate(-1)}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-subtle bg-surface text-muted-text transition hover:bg-hover hover:text-foreground"
              aria-label="返回"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Batch Detail</p>
              <h1 className="truncate text-lg font-semibold text-foreground">{run?.template_name || '跑批详情'}</h1>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleRegenerateReport()}
              isLoading={isRegenerating}
              loadingText="生成中..."
            >
              <FileText className="h-4 w-4" />
              重生成汇总
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleNotifyRun()}
              isLoading={isNotifying}
              loadingText="发送中..."
            >
              <Bell className="h-4 w-4" />
              发送通知
            </Button>
            <Button variant="secondary" size="sm" onClick={() => void loadRun()} isLoading={isLoading}>
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </div>
      </div>

      <main className="min-h-0 flex-1 overflow-hidden px-4 py-4">
        <div className="flex h-full w-full flex-col gap-4">
          {error && <ApiErrorAlert error={error} onDismiss={() => setError(null)} />}
          {actionMessage && (
            <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300">
              {actionMessage}
            </div>
          )}

          {isLoading && !run ? (
            <div className="flex min-h-[24rem] items-center justify-center rounded-xl border border-subtle bg-surface">
              <Loader2 className="h-5 w-5 animate-spin text-muted-text" />
            </div>
          ) : run && results.length > 0 ? (
            <>
              <section className="grid gap-3 sm:grid-cols-5">
                <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wider text-muted-text">股票数</p>
                  <p className="mt-1 text-xl font-semibold text-foreground">{run.stock_count}</p>
                </div>
                <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wider text-muted-text">成功</p>
                  <p className="mt-1 text-xl font-semibold text-emerald-600">{successCount}</p>
                </div>
                <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wider text-muted-text">失败</p>
                  <p className="mt-1 text-xl font-semibold text-red-600">{failedCount}</p>
                </div>
                <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wider text-muted-text">成功率</p>
                  <p className="mt-1 text-xl font-semibold text-foreground">{successRate}%</p>
                </div>
                <div className="rounded-lg border border-subtle bg-surface px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wider text-muted-text">筛选通过</p>
                  <p className="mt-1 text-xl font-semibold text-cyan-700">{passedCodes.length}</p>
                </div>
              </section>

              <div className="flex items-center gap-2 rounded-xl border border-subtle bg-surface p-1">
                <button
                  type="button"
                  onClick={() => setActiveView('summary')}
                  className={cn(
                    'h-8 rounded-lg px-3 text-sm transition-colors',
                    activeView === 'summary'
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-text hover:bg-hover hover:text-foreground',
                  )}
                >
                  汇总 MD
                </button>
                <button
                  type="button"
                  onClick={() => setActiveView('details')}
                  className={cn(
                    'h-8 rounded-lg px-3 text-sm transition-colors',
                    activeView === 'details'
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-text hover:bg-hover hover:text-foreground',
                  )}
                >
                  单股明细
                </button>
              </div>

              {activeView === 'summary' ? (
                <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-subtle bg-surface">
                  <div className="border-b border-subtle px-5 py-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                      <div>
                        <p className="text-sm font-semibold text-foreground">汇总统计 MD</p>
                        <p className="text-xs text-muted-text">通知同源的统计报告，包含整体完成、成功率和失败项。</p>
                      </div>
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <input
                          value={groupName}
                          onChange={(event) => setGroupName(event.target.value)}
                          placeholder={defaultGroupName}
                          className="h-9 min-w-[16rem] rounded-lg border border-subtle bg-background px-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                        />
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={handleCreatePassedGroup}
                          disabled={passedCodes.length === 0 || isCreatingGroup}
                        >
                          <FolderPlus className="h-4 w-4" />
                          {isCreatingGroup ? '入库中…' : `建股票池 (${passedCodes.length})`}
                        </Button>
                      </div>
                    </div>
                  </div>
                  <pre className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words px-5 py-4 text-sm leading-7 text-secondary-text">
                    {summaryMd || '暂无汇总报告'}
                  </pre>
                </section>
              ) : (
              <section className="grid min-h-0 flex-1 grid-rows-[minmax(12rem,34%)_minmax(0,1fr)] overflow-hidden rounded-xl border border-subtle bg-surface lg:grid-cols-[320px_minmax(0,1fr)] lg:grid-rows-1">
                <aside className="flex min-h-0 flex-col border-b border-subtle lg:border-b-0 lg:border-r">
                  <div className="border-b border-subtle px-4 py-3">
                    <p className="text-sm font-semibold text-foreground">单股结果</p>
                    <p className="text-xs text-muted-text">
                      {resultSearch.trim() ? `${filteredResults.length} / ${results.length} 条匹配` : `${results.length} 条已保存结果`}
                    </p>
                    <div className="relative mt-3">
                      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-text" />
                      <input
                        value={resultSearch}
                        onChange={(event) => setResultSearch(event.target.value)}
                        placeholder="搜索代码、摘要、状态..."
                        className="h-9 w-full rounded-lg border border-subtle bg-background pl-9 pr-9 text-sm text-foreground outline-none transition placeholder:text-muted-text/70 focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                      />
                      {resultSearch && (
                        <button
                          type="button"
                          onClick={() => setResultSearch('')}
                          className="absolute right-1.5 top-1/2 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-text transition hover:bg-hover hover:text-foreground"
                          aria-label="清空搜索"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    {filteredResults.length > 0 ? filteredResults.map((item) => (
                      <button
                        key={item.code}
                        type="button"
                        onClick={() => setSelectedCode(item.code)}
                        className={cn(
                          'flex w-full flex-col gap-1 border-b border-subtle px-4 py-3 text-left transition-colors hover:bg-hover/70',
                          selectedResult?.code === item.code && 'bg-primary/10',
                        )}
                      >
                        <span className="flex items-center justify-between gap-3">
                          <span className="font-mono text-sm font-semibold text-foreground">{item.code}</span>
                          <span className={cn(
                            'rounded-full px-2 py-0.5 text-[10px]',
                            item.success ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600',
                          )}
                          >
                            {item.success ? '成功' : '失败'}
                          </span>
                        </span>
                        <span className="line-clamp-2 text-xs leading-5 text-muted-text">{item.summary}</span>
                      </button>
                    )) : (
                      <div className="px-4 py-8 text-center text-sm text-muted-text">
                        没有匹配的单股结果
                      </div>
                    )}
                  </div>
                </aside>

                <article className="flex min-h-0 flex-col overflow-hidden">
                  {selectedResult ? (
                    <div className="flex h-full min-h-0 flex-col">
                      <div className="border-b border-subtle px-5 py-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <FileText className="h-4 w-4 text-muted-text" />
                          <h2 className="font-mono text-lg font-semibold text-foreground">{selectedResult.code}</h2>
                          <span className={cn(
                            'rounded-full px-2 py-0.5 text-xs',
                            selectedResult.success ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600',
                          )}
                          >
                            {selectedResult.success ? '分析成功' : '分析失败'}
                          </span>
                          <span className="text-xs text-muted-text">{selectedResult.model}</span>
                        </div>
                        <p className="mt-2 text-sm text-muted-text">{selectedResult.summary}</p>
                      </div>
                      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-5 py-4 text-sm leading-7 text-secondary-text">
                        {selectedResult.text || '无输出'}
                      </pre>
                    </div>
                  ) : null}
                </article>
              </section>
              )}
            </>
          ) : (
            <EmptyState
              title="没有可展示的跑批结果"
              description="这个跑批还没有保存单股输出，或记录已经被清理。"
              className="min-h-[24rem]"
            />
          )}
        </div>
      </main>
    </div>
  );
};

export default BatchRunDetailPage;
