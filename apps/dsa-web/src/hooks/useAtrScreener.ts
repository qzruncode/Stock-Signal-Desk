import { useCallback, useRef, useState } from 'react';
import { stocksApi } from '../api/stocks';
import { runAtrScreener, type AtrScreenResult } from '../utils/atr-screener';

export type ScreenPhase = 'idle' | 'screening' | 'done';

export interface ScreenProgress {
  stage: 'fetching_list' | 'processing' | 'fundamental_filtering';
  currentPage: number;
  totalPages: number;
  processed: number;
  total: number;
  matched: number;
  skipped: number;
  etaMinutes: number;
  fundamentalTotal?: number;
  fundamentalProcessed?: number;
  fundamentalFiltered?: number;
}

const STOCKS_PAGE_SIZE = 10;
const BATCH_SIZE = 50;
const THROTTLE_MS = 200;
const THROTTLE_COUNT = 50;
const LOG_MAX = 50;

export interface UseAtrScreenerOptions {
  onError: (msg: string) => void;
}

export interface UseAtrScreenerReturn {
  phase: ScreenPhase;
  progress: ScreenProgress | null;
  log: string[];
  result: AtrScreenResult | null;
  handleScreen: () => Promise<void>;
  handleCancel: () => void;
  handleReset: () => void;
}

export function useAtrScreener({ onError }: UseAtrScreenerOptions): UseAtrScreenerReturn {
  const [phase, setPhase] = useState<ScreenPhase>('idle');
  const [progress, setProgress] = useState<ScreenProgress | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<AtrScreenResult | null>(null);

  const cancelledRef = useRef(false);
  const matchedRef = useRef(0);
  const skippedRef = useRef(0);
  const processedRef = useRef(0);
  const totalRef = useRef(0);
  const matchedCodesRef = useRef<string[]>([]);
  const lastFlushRef = useRef(0);
  const lastFlushCountRef = useRef(0);
  const t0Ref = useRef(0);

  const flushProgress = useCallback((overrides?: Partial<ScreenProgress>) => {
    const now = Date.now();
    const elapsed = (now - t0Ref.current) / 1000;
    const p = processedRef.current;
    const total = totalRef.current;
    const rate = elapsed > 0 ? p / elapsed : 1;
    const remaining = total - p;
    const etaMin = rate > 0 ? Math.ceil(remaining / rate / 60) : 0;

    setProgress((prev) => ({
      stage: prev?.stage ?? 'processing',
      currentPage: prev?.currentPage ?? 1,
      totalPages: prev?.totalPages ?? 1,
      processed: p,
      total,
      matched: matchedRef.current,
      skipped: skippedRef.current,
      etaMinutes: etaMin,
      ...overrides,
    }));
    lastFlushRef.current = now;
    lastFlushCountRef.current = p;
  }, []);

  const maybeFlush = useCallback(
    (overrides?: Partial<ScreenProgress>) => {
      const now = Date.now();
      const countDelta = processedRef.current - lastFlushCountRef.current;
      const timeDelta = now - lastFlushRef.current;
      if (timeDelta >= THROTTLE_MS || countDelta >= THROTTLE_COUNT) {
        flushProgress(overrides);
      }
    },
    [flushProgress],
  );

  const handleScreen = useCallback(async () => {
    cancelledRef.current = false;
    matchedRef.current = 0;
    skippedRef.current = 0;
    processedRef.current = 0;
    matchedCodesRef.current = [];
    lastFlushRef.current = 0;
    lastFlushCountRef.current = 0;
    t0Ref.current = Date.now();

    setPhase('screening');
    setLog([]);
    setResult(null);

    try {
      const allStocks: string[] = [];
      let page = 1;
      let totalPages = 1;

      setProgress({
        stage: 'fetching_list',
        currentPage: 1,
        totalPages: 1,
        processed: 0,
        total: 0,
        matched: 0,
        skipped: 0,
        etaMinutes: 0,
      });

      while (page <= totalPages && !cancelledRef.current) {
        const res = await stocksApi.list({ page, page_size: STOCKS_PAGE_SIZE });
        totalPages = res.total_pages;
        allStocks.push(...res.items.map((s) => s.code));

        page++;

        setProgress((prev) =>
          prev
            ? { ...prev, currentPage: Math.min(page, totalPages), totalPages }
            : prev,
        );
      }

      if (cancelledRef.current || allStocks.length === 0) {
        setPhase('idle');
        setProgress(null);
        return;
      }

      totalRef.current = allStocks.length;
      lastFlushRef.current = Date.now();

      let idx = 0;
      let batchIdx = 0;

      while (idx < allStocks.length) {
        if (cancelledRef.current) break;
        batchIdx++;

        const end = Math.min(idx + BATCH_SIZE, allStocks.length);
        const batch = allStocks.slice(idx, end);

        try {
          const res = await stocksApi.getKlineBatch(batch, 250);

          if (cancelledRef.current) break;

          for (const code of batch) {
            const klines = res.results[code] || [];
            const detail = runAtrScreener({ [code]: klines });
            processedRef.current++;

            if (detail.matchedCodes.length > 0) {
              matchedRef.current++;
              matchedCodesRef.current.push(code);
            } else if (detail.totalAnalyzed === 0) {
              skippedRef.current++;
            }

            setLog((prev) => {
              const entry = detail.matchedCodes.length > 0
                ? `✅ ${code} 符合`
                : detail.totalAnalyzed === 0
                  ? klines.length > 0
                    ? `🆕 ${code} 上市不足(${klines.length}天)`
                    : `⚠️ ${code} 无数据`
                  : `${code} 不符`;
              const next = [...prev, entry];
              return next.length > LOG_MAX ? next.slice(next.length - LOG_MAX) : next;
            });
          }
        } catch {
          for (const code of batch) {
            processedRef.current++;
            skippedRef.current++;
            setLog((prev) => {
              const next = [...prev, `❌ ${code} 请求失败`];
              return next.length > LOG_MAX ? next.slice(next.length - LOG_MAX) : next;
            });
          }
        }

        maybeFlush({ currentPage: batchIdx });
        idx = end;
      }

      flushProgress();

      if (cancelledRef.current) {
        setResult({
          matchedCodes: [...matchedCodesRef.current],
          stockDetails: [],
          totalAnalyzed: processedRef.current,
          totalListed: totalRef.current,
        });
        setPhase('idle');
        setProgress(null);
        return;
      }

      const atrResult = {
        matchedCodes: [...matchedCodesRef.current],
        stockDetails: [],
        totalAnalyzed: processedRef.current,
        totalListed: totalRef.current,
      };

      if (atrResult.matchedCodes.length > 0 && !cancelledRef.current) {
        setLog(['', '═══ Phase 3: 基本面筛选 ═══', '']);

        const codesToFilter = atrResult.matchedCodes;
        const FUNDAMENTAL_BATCH = 20;
        const FUNDAMENTAL_RETRY = 3;
        let passedCount = 0;
        let rejectedCount = 0;
        let noDataCount = 0;
        let failedBatches = 0;
        let failedCodesCount = 0;
        const phase3PassedCodes: string[] = [];

        setProgress((prev) =>
          prev
            ? {
                ...prev,
                stage: 'fundamental_filtering',
                fundamentalTotal: codesToFilter.length,
                fundamentalProcessed: 0,
                fundamentalFiltered: 0,
              }
            : prev,
        );

        let idx = 0;
        while (idx < codesToFilter.length && !cancelledRef.current) {
          const batch = codesToFilter.slice(idx, idx + FUNDAMENTAL_BATCH);

          let batchSuccess = false;
          for (let attempt = 0; attempt < FUNDAMENTAL_RETRY && !batchSuccess; attempt++) {
            try {
              const response = await stocksApi.fundamentalFilter(batch);
              batchSuccess = true;

              for (const code of batch) {
                const data = response.data[code] as { revenue_latest: number | null; net_profit_latest: number | null; debt_ratio: number | null } | undefined;
                if (!data || data.revenue_latest == null) {
                  noDataCount++;
                  setLog((prev) => [...prev, `⚠️ ${code}: 无财务数据`]);
                } else {
                  let passed = true;
                  if (data.revenue_latest <= 500_000_000) passed = false;
                  if (data.net_profit_latest == null || data.net_profit_latest <= 0) passed = false;
                  if (data.debt_ratio == null || data.debt_ratio >= 70) passed = false;

                  if (passed) {
                    passedCount++;
                    phase3PassedCodes.push(code);
                    setLog((prev) => [...prev, `✅ ${code}: 通过`]);
                  } else {
                    rejectedCount++;
                    const reasons: string[] = [];
                    if (data.revenue_latest <= 500_000_000) reasons.push('营收不足');
                    if (data.net_profit_latest == null || data.net_profit_latest <= 0) reasons.push('净利润为负');
                    if (data.debt_ratio != null && data.debt_ratio >= 70) reasons.push('资产负债率超标');
                    setLog((prev) => [...prev, `❌ ${code}: ${reasons.join(', ')}`]);
                  }
                }
              }
            } catch (err) {
              if (attempt < FUNDAMENTAL_RETRY - 1) {
                const delay = (attempt + 1) * 1000;
                setLog((prev) => [...prev, `🔄 第 ${Math.floor(idx / FUNDAMENTAL_BATCH) + 1} 批失败，${delay / 1000}s 后重试 (${attempt + 1}/${FUNDAMENTAL_RETRY - 1}): ${err instanceof Error ? err.message : '未知错误'}`]);
                await new Promise(r => setTimeout(r, delay));
              } else {
                setLog((prev) => [
                  ...prev,
                  `❌ 第 ${Math.floor(idx / FUNDAMENTAL_BATCH) + 1} 批重试 ${FUNDAMENTAL_RETRY} 次后放弃: ${err instanceof Error ? err.message : '未知错误'}`,
                ]);
                failedBatches++;
                failedCodesCount += batch.length;
              }
            }
          }

          if (batchSuccess) {
            idx += FUNDAMENTAL_BATCH;
          } else {
            idx += FUNDAMENTAL_BATCH;
          }

          setProgress((prev) =>
            prev
              ? {
                  ...prev,
                  fundamentalProcessed: Math.min(idx, codesToFilter.length),
                  fundamentalFiltered: passedCount,
                }
              : prev,
          );

          await new Promise(r => setTimeout(r, 0));
        }

        if (cancelledRef.current) {
          setLog((prev) => [...prev, '→ 用户取消基本面筛选']);
          setResult(atrResult);
        } else {
          const summary = `✅ 基本面筛选完成: ${passedCount} 只通过，${rejectedCount} 只被剔除，${noDataCount} 只无数据${failedBatches > 0 ? `，${failedBatches} 批（${failedCodesCount} 只）跳过` : ''}`;
          setLog((prev) => [...prev, '', summary]);
          setResult({
            matchedCodes: phase3PassedCodes,
            stockDetails: [],
            totalAnalyzed: atrResult.totalAnalyzed,
            totalListed: atrResult.totalListed,
          });
        }
      } else {
        setResult(atrResult);
      }

      setPhase('done');
    } catch (err: unknown) {
      if (!cancelledRef.current) {
        onError(err instanceof Error ? err.message : 'ATR 选股失败');
      }
      setPhase('idle');
      setProgress(null);
    }
  }, [onError, maybeFlush, flushProgress]);

  const handleCancel = useCallback(() => {
    cancelledRef.current = true;
  }, []);

  const handleReset = useCallback(() => {
    setPhase('idle');
    setProgress(null);
    setLog([]);
    setResult(null);
    cancelledRef.current = true;
  }, []);

  return {
    phase,
    progress,
    log,
    result,
    handleScreen,
    handleCancel,
    handleReset,
  };
}
