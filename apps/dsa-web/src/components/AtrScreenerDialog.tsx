import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Zap, Loader2, XCircle } from 'lucide-react';
import { stocksApi } from '../api/stocks';
import { runAtrScreener, type AtrScreenResult } from '../utils/atr-screener';
import { upsertWatchlistGroup } from '../utils/watchlistGroups';
import { cn } from '../utils/cn';
import { Modal } from './common/Modal';
import { Button } from './common/Button';
import { classifyStock, MARKET_COLORS } from '../utils/market';

interface AtrScreenerDialogProps {
  onError: (msg: string) => void;
  onSuccess: (msg: string) => void;
  onGroupSelect: (groupId: string) => void;
  isOpen: boolean;
  onClose: () => void;
}

type ScreenPhase = 'idle' | 'screening' | 'done';

interface ScreenProgress {
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

const SCREEN_GROUP_NAME = '高波动股';
const STOCKS_PAGE_SIZE = 10;
const BATCH_SIZE = 50;
const THROTTLE_MS = 200;
const THROTTLE_COUNT = 50;
const LOG_MAX = 50;

const AtrScreenerDialog: React.FC<AtrScreenerDialogProps> = ({
  onError,
  onSuccess,
  onGroupSelect,
  isOpen,
  onClose,
}) => {
  const [phase, setPhase] = useState<ScreenPhase>('idle');
  const [progress, setProgress] = useState<ScreenProgress | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<AtrScreenResult | null>(null);
  const [groupName, setGroupName] = useState(SCREEN_GROUP_NAME);
  const [isSaving, setIsSaving] = useState(false);

  // Refs for async loop (avoid stale closures)
  const cancelledRef = useRef(false);
  const matchedRef = useRef(0);
  const skippedRef = useRef(0);
  const processedRef = useRef(0);
  const totalRef = useRef(0);
  const matchedCodesRef = useRef<string[]>([]);
  const lastFlushRef = useRef(0);
  const lastFlushCountRef = useRef(0);
  const t0Ref = useRef(0);
  const stockMetaMapRef = useRef<Record<string, { total_market_cap: number | null }>>({});

  // Throttled progress flush
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

  // Maybe flush: called after each batch, decides whether to update state
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

  // Main screening loop
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
      // Step 1: Fetch all stock codes via pagination
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

        // Save stock meta for pre-filter
        for (const s of res.items) {
          stockMetaMapRef.current[s.code] = { total_market_cap: s.total_market_cap };
        }

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

      // Step 2: Process stocks in batches via POST /stocks/kline/batch
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

          // Process each stock's kline data from the batch response
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

            // Log entry
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
          // Batch request failed — mark all stocks in batch as failed
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

      // Final flush
      flushProgress();

      if (cancelledRef.current) {
        setResult({
          matchedCodes: [...matchedCodesRef.current],
          stockDetails: [],
          totalAnalyzed: processedRef.current,
        });
        setPhase('idle');
        setProgress(null);
        return;
      }

      // Build final result
      const atrResult = {
        matchedCodes: [...matchedCodesRef.current],
        stockDetails: [],
        totalAnalyzed: processedRef.current,
      };

      // Phase 3: Fundamental filter (2筛) — frontend filtering
      if (atrResult.matchedCodes.length > 0 && !cancelledRef.current) {
        // Clear Phase 2 log to avoid LOG_MAX truncation and keep UI clean
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

          // Retry loop for transient failures (timeout, network errors)
          let batchSuccess = false;
          for (let attempt = 0; attempt < FUNDAMENTAL_RETRY && !batchSuccess; attempt++) {
            try {
              const response = await stocksApi.fundamentalFilter(batch);
              batchSuccess = true;

              // Frontend filtering \u2014 3 criteria
              for (const code of batch) {
                const data = response.data[code] as { revenue_ttm: number | null; deducted_profit_ttm: number | null; debt_ratio: number | null } | undefined;
                if (!data || data.revenue_ttm == null) {
                  noDataCount++;
                  setLog((prev) => [...prev, `\u26a0\ufe0f ${code}: \u65e0\u8d22\u52a1\u6570\u636e`]);
                } else {
                  let passed = true;
                  if (data.revenue_ttm <= 500_000_000) passed = false;
                  if (data.deducted_profit_ttm == null || data.deducted_profit_ttm <= 0) passed = false;
                  if (data.debt_ratio == null || data.debt_ratio >= 70) passed = false;

                  if (passed) {
                    passedCount++;
                    phase3PassedCodes.push(code);
                    setLog((prev) => [...prev, `\u2705 ${code}: \u901a\u8fc7`]);
                  } else {
                    rejectedCount++;
                    const reasons: string[] = [];
                    if (data.revenue_ttm <= 500_000_000) reasons.push('\u8425\u6536TTM\u4e0d\u8db3');
                    if (data.deducted_profit_ttm == null || data.deducted_profit_ttm <= 0) reasons.push('\u6263\u975e\u51c0\u5229\u6da6\u4e3a\u8d1f');
                    if (data.debt_ratio != null && data.debt_ratio >= 70) reasons.push('\u8d44\u4ea7\u8d1f\u503a\u7387\u8d85\u6807');
                    setLog((prev) => [...prev, `\u274c ${code}: ${reasons.join(', ')}`]);
                  }
                }
              }
            } catch (err) {
              if (attempt < FUNDAMENTAL_RETRY - 1) {
                const delay = (attempt + 1) * 1000;
                setLog((prev) => [...prev, `\ud83d\udd04 \u7b2c ${Math.floor(idx / FUNDAMENTAL_BATCH) + 1} \u6279\u5931\u8d25\uff0c${delay / 1000}s \u540e\u91cd\u8bd5 (${attempt + 1}/${FUNDAMENTAL_RETRY - 1}): ${err instanceof Error ? err.message : '\u672a\u77e5\u9519\u8bef'}`]);
                await new Promise(r => setTimeout(r, delay));
              } else {
                setLog((prev) => [
                  ...prev,
                  `\u274c \u7b2c ${Math.floor(idx / FUNDAMENTAL_BATCH) + 1} \u6279\u91cd\u8bd5 ${FUNDAMENTAL_RETRY} \u6b21\u540e\u653e\u5f03: ${err instanceof Error ? err.message : '\u672a\u77e5\u9519\u8bef'}`,
                ]);
                failedBatches++;
                failedCodesCount += batch.length;
              }
            }
          }

          if (batchSuccess) {
            idx += FUNDAMENTAL_BATCH;

            setProgress((prev) =>
              prev
                ? {
                    ...prev,
                    fundamentalProcessed: Math.min(idx, codesToFilter.length),
                    fundamentalFiltered: passedCount,
                  }
                : prev,
            );
          } else {
            // Batch failed after all retries \u2014 skip these codes but continue loop
            idx += FUNDAMENTAL_BATCH;

            setProgress((prev) =>
              prev
                ? {
                    ...prev,
                    fundamentalProcessed: Math.min(idx, codesToFilter.length),
                    fundamentalFiltered: passedCount,
                  }
                : prev,
            );
          }

          // Yield to let React render log updates
          await new Promise(r => setTimeout(r, 0));
        }

        if (cancelledRef.current) {
          setLog((prev) => [...prev, '\u2192 \u7528\u6237\u53d6\u6d88\u57fa\u672c\u9762\u7b5b\u9009']);
          setResult(atrResult);
        } else {
          const summary = `\u2705 \u57fa\u672c\u9762\u7b5b\u9009\u5b8c\u6210: ${passedCount} \u53ea\u901a\u8fc7\uff0c${rejectedCount} \u53ea\u88ab\u5254\u9664\uff0c${noDataCount} \u53ea\u65e0\u6570\u636e${failedBatches > 0 ? `\uff0c${failedBatches} \u6279\uff08${failedCodesCount} \u53ea\uff09\u8df3\u8fc7` : ''}`;
          setLog((prev) => [...prev, '', summary]);
          setResult({
            matchedCodes: phase3PassedCodes,
            stockDetails: [],
            totalAnalyzed: atrResult.totalAnalyzed,
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

  // Cancel screening
  const handleCancel = useCallback(() => {
    cancelledRef.current = true;
  }, []);

  // Create group from results
  const handleCreateGroup = useCallback(async () => {
    if (!result || isSaving) return;
    const name = groupName.trim() || SCREEN_GROUP_NAME;
    const matchedCodes = result.matchedCodes;
    setIsSaving(true);
    try {
      // Backend upserts by name: same name replaces the group's codes.
      const group = await upsertWatchlistGroup(name, matchedCodes, 'screener');
      onGroupSelect(group.id);
      onSuccess(`已保存分组「${name}」，${matchedCodes.length} 只股票`);
      onClose();
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : '保存分组失败');
    } finally {
      setIsSaving(false);
    }
  }, [result, isSaving, groupName, onGroupSelect, onSuccess, onError, onClose]);

  const handleReset = useCallback(() => {
    setPhase('idle');
    setProgress(null);
    setLog([]);
    setResult(null);
    setGroupName(SCREEN_GROUP_NAME);
    cancelledRef.current = true;
  }, []);

  // Re-evaluate on open
  useEffect(() => {
    if (isOpen && phase === 'idle' && result) {
      setPhase('done');
    }
  }, [isOpen, phase, result]);

  // ─── Render ───

  const renderIdle = () => (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <Zap className="h-5 w-5 text-orange-500 mt-0.5 shrink-0" />
        <div className="text-sm">
          <p className="text-slate-700 font-medium">Phase 1+2: ATR 相对波动率</p>
          <p className="text-slate-700">
            筛选条件：250 日内 ATR% {'>'} 2.8 的天数占比 ≥ 60%
          </p>
          <p className="text-slate-700 font-medium mt-2">Phase 3: 基本面 2 筛（7项）</p>
          <ul className="text-xs text-slate-500 mt-1 space-y-0.5">
            <li>• 营收TTM {'>'} 5亿</li>
            <li>• 扣非净利润TTM {'>'} 0</li>
            <li>• 资产负债率 {'<'} 70%</li>
          </ul>
        </div>
      </div>
      <Button variant="home-action-ai" onClick={handleScreen} className="w-full">
        <Zap className="h-4 w-4" />
        开始筛选
      </Button>
    </div>
  );

  const renderScreening = () => {
    if (!progress) return null;
    const pct = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0;

    return (
      <div className="space-y-4">
        {/* Progress info */}
        <div className="text-sm space-y-1">
          {progress.stage === 'fetching_list' ? (
            <p className="text-slate-600">
              正在获取股票列表... 第 {progress.currentPage}/{progress.totalPages} 页
            </p>
          ) : progress.stage === 'fundamental_filtering' ? (
            <>
              <p className="text-slate-600">
                基本面筛选中... {progress.fundamentalProcessed ?? 0}/{progress.fundamentalTotal ?? 0} 只
              </p>
            </>
          ) : (
            <>
              <p className="text-slate-600">
                已处理 {progress.processed}/{progress.total} 只 · {progress.matched} 只符合条件
              </p>
              <p className="text-xs text-slate-400">
                预计约 {progress.etaMinutes} 分钟 · 跳过 {progress.skipped} 只
              </p>
            </>
          )}
        </div>

        {/* Progress bar */}
        <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-linear-to-r from-cyan-500 to-indigo-500 rounded-full transition-all duration-300"
            style={{
              width: progress.stage === 'fundamental_filtering'
                ? `${progress.fundamentalTotal && progress.fundamentalTotal > 0
                    ? Math.round(((progress.fundamentalProcessed ?? 0) / progress.fundamentalTotal) * 100)
                    : 0}%`
                : `${pct}%`,
            }}
          />
        </div>

        {/* Scrolling log */}
        {log.length > 0 && (
          <div className="max-h-[30vh] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-[11px] leading-relaxed text-slate-600">
            {log.map((entry, i) => (
              <div key={i} className={cn(
                entry.startsWith('✅') ? 'text-emerald-600' :
                entry.startsWith('❌') ? 'text-red-500' :
                entry.startsWith('⚠️') ? 'text-amber-500' :
                entry.startsWith('🆕') ? 'text-orange-500' :
                'text-slate-400',
              )}>
                {entry}
              </div>
            ))}
          </div>
        )}

        {/* Cancel button */}
        <button
          type="button"
          onClick={handleCancel}
          className="flex items-center gap-1.5 mx-auto text-xs text-slate-400 hover:text-red-500 transition-colors"
        >
          <XCircle className="h-3.5 w-3.5" />
          取消筛选
        </button>
      </div>
    );
  };

  const renderResult = () => {
    if (!result) return null;
    const displayStocks = result.matchedCodes.map((code) => {
      const { market, marketLabel } = classifyStock(code);
      return { code, market, marketLabel };
    });

    return (
      <div className="space-y-4">
        {/* Summary */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">共分析 {result.totalAnalyzed} 只，</span>
          <span className="font-medium text-orange-600">{result.matchedCodes.length} 只符合条件</span>
          {result.totalAnalyzed < totalRef.current && (
            <span className="text-xs text-slate-400">
              （ATR 阶段跳过 {totalRef.current - result.totalAnalyzed} 只）
            </span>
          )}
        </div>

        {/* Stock list */}
        {displayStocks.length > 0 && (
          <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50/50 p-3">
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {displayStocks.map((stock) => (
                <div
                  key={stock.code}
                  className="flex items-center gap-2 rounded-lg border border-slate-100 bg-white px-2.5 py-1.5 text-xs"
                >
                  <span className="font-mono font-medium text-slate-700">{stock.code}</span>
                  <span className={cn(
                    'inline-flex rounded px-1 py-0.5 text-[10px] font-medium',
                    MARKET_COLORS[stock.market] || '',
                  )}>
                    {stock.marketLabel}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Group creation */}
        <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3">
          <label className="text-xs font-medium text-slate-600">创建分组</label>
          <div className="flex gap-2">
            <input
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
              placeholder="分组名称"
            />
            <Button
              variant="home-action-ai"
              size="sm"
              onClick={handleCreateGroup}
              disabled={!result.matchedCodes.length || isSaving}
            >
              {isSaving ? '入库中…' : '确定入库'}
            </Button>
          </div>
        </div>

        {/* Reset button */}
        <button
          type="button"
          onClick={handleReset}
          className="text-xs text-slate-400 hover:text-cyan-600 transition-colors"
        >
          重新筛选
        </button>
      </div>
    );
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="ATR 相对波动率选股"
      width="max-w-xl"
      preventClose={phase === 'screening'}
      footer={
        phase === 'screening' && progress ? (
          <div className="flex items-center gap-2 text-sm text-orange-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>
              {progress.stage === 'fetching_list'
                ? `正在获取股票列表... ${progress.currentPage}/${progress.totalPages}`
                : progress.stage === 'fundamental_filtering'
                  ? `基本面筛选中... ${(progress.fundamentalProcessed ?? 0)}/${progress.fundamentalTotal ?? 0} 只`
                  : `已处理 ${progress.processed}/${progress.total} 只，${progress.matched} 只符合`}
            </span>
          </div>
        ) : undefined
      }
    >
      {phase === 'idle' && renderIdle()}
      {phase === 'screening' && renderScreening()}
      {phase === 'done' && renderResult()}
    </Modal>
  );
};

export default AtrScreenerDialog;
