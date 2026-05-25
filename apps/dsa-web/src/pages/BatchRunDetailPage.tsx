import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, FileText, Loader2, RefreshCw } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { batchApi, type BatchRunItem } from '../api/batch';
import { ApiErrorAlert, Button, EmptyState } from '../components/common';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { cn } from '../utils/cn';

interface BatchResultItem {
  code: string;
  success: boolean;
  model: string;
  text: string;
  summary: string;
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
    const parsed = JSON.parse(raw) as Record<string, { success?: boolean; model?: string; text?: string }>;
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
        };
      });
  } catch {
    return [];
  }
}

const BatchRunDetailPage: React.FC = () => {
  const navigate = useNavigate();
  const { runId = '' } = useParams();
  const [run, setRun] = useState<BatchRunItem | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadRun = useCallback(async () => {
    if (!runId) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await batchApi.getRunDetail(runId);
      setRun(data);
      const first = parseBatchResults(data.results_json)[0];
      setSelectedCode((current) => current || first?.code || null);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    document.title = '跑批详情 - DSA';
    void loadRun();
  }, [loadRun]);

  const results = useMemo(() => parseBatchResults(run?.results_json), [run]);
  const selectedResult = useMemo(
    () => results.find((item) => item.code === selectedCode) || results[0] || null,
    [results, selectedCode],
  );
  const failedCount = results.filter((item) => !item.success).length;
  const successCount = results.length - failedCount;
  const successRate = run?.stock_count ? ((run.success_count / run.stock_count) * 100).toFixed(1) : '0.0';

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-base">
      <div className="border-b border-[#dbe3ed] bg-[#fbfcfe]/90 px-4 py-3 backdrop-blur-xl">
        <div className="mx-auto flex w-full max-w-[1440px] flex-wrap items-center justify-between gap-3">
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
          <Button variant="secondary" size="sm" onClick={() => void loadRun()} isLoading={isLoading}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </Button>
        </div>
      </div>

      <main className="min-h-0 flex-1 overflow-hidden px-4 py-4">
        <div className="mx-auto flex h-full w-full max-w-[1440px] flex-col gap-4">
          {error && <ApiErrorAlert error={error} onDismiss={() => setError(null)} />}

          {isLoading && !run ? (
            <div className="flex min-h-[24rem] items-center justify-center rounded-xl border border-subtle bg-surface">
              <Loader2 className="h-5 w-5 animate-spin text-muted-text" />
            </div>
          ) : run && results.length > 0 ? (
            <>
              <section className="grid gap-3 sm:grid-cols-4">
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
              </section>

              <section className="grid min-h-0 flex-1 overflow-hidden rounded-xl border border-subtle bg-surface lg:grid-cols-[320px_1fr]">
                <aside className="min-h-0 border-b border-subtle lg:border-b-0 lg:border-r">
                  <div className="border-b border-subtle px-4 py-3">
                    <p className="text-sm font-semibold text-foreground">单股结果</p>
                    <p className="text-xs text-muted-text">{results.length} 条已保存结果</p>
                  </div>
                  <div className="max-h-[320px] overflow-y-auto lg:max-h-full">
                    {results.map((item) => (
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
                    ))}
                  </div>
                </aside>

                <article className="min-h-0 overflow-hidden">
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
