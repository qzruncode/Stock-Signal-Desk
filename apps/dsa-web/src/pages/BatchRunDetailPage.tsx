import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Bell, FileText, Loader2, RefreshCw } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { batchApi } from '../api/batch';
import { ApiErrorAlert, Button, EmptyState } from '../components/common';
import { StatsCards, ViewTabs, SummaryView, DetailList, DetailView } from '../components/batch';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { parseBatchResults, extractPassedCodesFromSummary } from '../utils/batch';
import { upsertWatchlistGroup } from '../utils/watchlistGroups';

const BatchRunDetailPage: React.FC = () => {
  const navigate = useNavigate();
  const { runId = '' } = useParams();
  const [run, setRun] = useState<Awaited<ReturnType<typeof batchApi.getRunDetail>> | null>(null);
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
  const [isCreatingGroup, setIsCreatingGroup] = useState(false);
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
    document.title = '跑批详情 - Stock Assistant';
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
      <div className="border-b border-slate-200 bg-slate-50/90 px-4 py-3 backdrop-blur-xl">
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
            <Button variant="secondary" size="sm" onClick={() => void handleRegenerateReport()} isLoading={isRegenerating} loadingText="生成中...">
              <FileText className="h-4 w-4" />
              重生成汇总
            </Button>
            <Button variant="secondary" size="sm" onClick={() => void handleNotifyRun()} isLoading={isNotifying} loadingText="发送中...">
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
              <StatsCards
                run={run}
                successCount={successCount}
                failedCount={failedCount}
                successRate={successRate}
                passedCount={passedCodes.length}
              />
              <ViewTabs activeView={activeView} onChange={setActiveView} />

              {activeView === 'summary' ? (
                <SummaryView
                  summaryMd={summaryMd}
                  groupName={groupName}
                  defaultGroupName={defaultGroupName}
                  passedCodesLength={passedCodes.length}
                  isCreatingGroup={isCreatingGroup}
                  onGroupNameChange={setGroupName}
                  onCreateGroup={handleCreatePassedGroup}
                />
              ) : (
                <section className="grid min-h-0 flex-1 grid-rows-[minmax(12rem,34%)_minmax(0,1fr)] overflow-hidden rounded-xl border border-subtle bg-surface lg:grid-cols-[320px_minmax(0,1fr)] lg:grid-rows-1">
                  <DetailList
                    results={results}
                    filteredResults={filteredResults}
                    selectedCode={selectedCode}
                    resultSearch={resultSearch}
                    onSearchChange={setResultSearch}
                    onSelectCode={setSelectedCode}
                  />
                  <article className="flex min-h-0 flex-col overflow-hidden">
                    <DetailView result={selectedResult} />
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