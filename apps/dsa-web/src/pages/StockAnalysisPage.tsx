import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Clock,
  DollarSign,
  TrendingUp,
} from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Select } from '../components/common';
import { quotesApi, type RealtimeQuote } from '../api/quotes';
import { klineApi, type KlineResponse } from '../api/kline';
import KLineChartPanel from '../components/KLineChartPanel';
import { cn } from '../utils/cn';
import { classifyStock, MARKET_LABELS, MARKET_COLORS } from '../utils/market';

// ---------------------------------------------------------------------------
// Formatters (shared)
// ---------------------------------------------------------------------------

function formatMarketCap(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

function formatVolume(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿手`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万手`;
  return `${value}手`;
}

function formatAmount(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

function DataItem({
  label,
  value,
  highlight,
  highlightUp,
  highlightDown,
}: {
  label: string;
  value: string;
  highlight?: boolean;
  highlightUp?: boolean;
  highlightDown?: boolean;
}) {
  const valueColor = highlightUp
    ? 'text-red-600 font-semibold'
    : highlightDown
      ? 'text-green-600 font-semibold'
      : highlight
        ? 'text-slate-900 font-semibold'
        : 'text-slate-700';
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={cn('tabular-nums text-sm', valueColor)}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RealtimeQuotePanel
// ---------------------------------------------------------------------------

function RealtimeQuotePanel({ quote }: { quote: RealtimeQuote }) {
  const changePct = quote.change_pct ?? 0;
  const isUp = changePct > 0;
  const isDown = changePct < 0;
  const changeColor = isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-slate-500';
  const changeBg = isUp ? 'bg-red-50' : isDown ? 'bg-green-50' : 'bg-slate-50';
  const { market } = classifyStock(quote.code);
  const limitRatio = market === 'cyb' || market === 'kcb' ? 0.2 : market === 'bj' ? 0.3 : quote.name.includes('ST') ? 0.05 : 0.1;
  const preClose = quote.pre_close ?? quote.price;
  const limitUpPrice = preClose ? Math.round(preClose * (1 + limitRatio) * 100) / 100 : null;
  const limitDownPrice = preClose ? Math.round(preClose * (1 - limitRatio) * 100) / 100 : null;

  return (
    <div className="space-y-4">
      <div className={cn('rounded-2xl border p-6 shadow-sm', changeBg)}>
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-lg font-semibold text-slate-700">{quote.code}</span>
              <span className={cn('inline-flex rounded-md px-2 py-0.5 text-xs font-medium', MARKET_COLORS[market] || '')}>
                {MARKET_LABELS[market] || market}
              </span>
            </div>
            <h2 className="mt-1 text-2xl font-bold text-slate-900">{quote.name}</h2>
          </div>
          <div className="text-right">
            <div className={cn('text-4xl font-bold tabular-nums', changeColor)}>
              {quote.price != null ? quote.price.toFixed(2) : '-'}
            </div>
            <div className={cn('mt-1 flex items-center gap-2 justify-end text-sm font-medium', changeColor)}>
              {isUp ? <ArrowUp className="h-4 w-4" /> : isDown ? <ArrowDown className="h-4 w-4" /> : null}
              <span>{changePct != null ? `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%` : '-'}</span>
              <span className="text-slate-400">
                {quote.change_amount != null ? `${quote.change_amount > 0 ? '+' : ''}${quote.change_amount.toFixed(2)}` : '-'}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <BarChart3 className="h-4 w-4 text-cyan-600" />交易数据
        </h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <DataItem label="成交量" value={formatVolume(quote.volume)} />
          <DataItem label="成交额" value={formatAmount(quote.amount)} />
          <DataItem label="换手率" value={quote.turnover_rate != null ? `${quote.turnover_rate.toFixed(2)}%` : '-'} />
          <DataItem label="量比" value={quote.volume_ratio != null ? quote.volume_ratio.toFixed(2) : '-'} />
          <DataItem label="振幅" value={quote.amplitude != null ? `${quote.amplitude.toFixed(2)}%` : '-'} />
          <DataItem label="市盈率(动)" value={quote.pe_ratio != null ? quote.pe_ratio.toFixed(2) : '-'} />
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <TrendingUp className="h-4 w-4 text-cyan-600" />价格区间
        </h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <DataItem label="今开" value={quote.open_price != null ? quote.open_price.toFixed(2) : '-'} />
          <DataItem label="昨收" value={preClose != null ? preClose.toFixed(2) : '-'} />
          <DataItem label="最高" value={quote.high != null ? quote.high.toFixed(2) : '-'} />
          <DataItem label="最低" value={quote.low != null ? quote.low.toFixed(2) : '-'} />
          <DataItem label="涨停价" value={limitUpPrice != null ? limitUpPrice.toFixed(2) : '-'} highlightUp />
          <DataItem label="跌停价" value={limitDownPrice != null ? limitDownPrice.toFixed(2) : '-'} highlightDown />
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <DollarSign className="h-4 w-4 text-cyan-600" />市值与估值
        </h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <DataItem label="总市值" value={formatMarketCap(quote.total_mv)} />
          <DataItem label="流通市值" value={formatMarketCap(quote.circ_mv)} />
          <DataItem label="市净率" value={quote.pb_ratio != null ? quote.pb_ratio.toFixed(2) : '-'} />
        </div>
      </div>

      <div className="flex items-center gap-1.5 text-xs text-slate-400">
        <Clock className="h-3 w-3" />
        <span>
          数据获取时间: {quote._fetched_at ? new Date(quote._fetched_at).toLocaleString('zh-CN') : '-'}
          {quote._cached ? ' · 缓存' : ' · 实时'}
        </span>
        {quote.source && (
          <>
            <span className="text-slate-300">|</span>
            <span>数据源: {quote.source}</span>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

type AnalysisMode = 'realtime' | 'kline';

const StockAnalysisPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchValue, setSearchValue] = useState('');

  // --- Realtime quote state ---
  const [quote, setQuote] = useState<RealtimeQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);

  // --- K-line state ---
  const [klineData, setKlineData] = useState<KlineResponse | null>(null);
  const [klineLoading, setKlineLoading] = useState(false);
  const [klineError, setKlineError] = useState<string | null>(null);
  // --- Tab mode ---
  const [mode, setMode] = useState<AnalysisMode>('realtime');

  const selectedSymbol = searchParams.get('symbol');

  // Sync search value from URL (only fetch realtime on initial load)
  useEffect(() => {
    const symbol = searchParams.get('symbol');
    if (symbol) {
      setSearchValue(symbol);
      void fetchQuote(symbol);
      // K-line is fetched lazily when user switches to kline tab
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Fetch K-line when mode switches to 'kline' ---
  useEffect(() => {
    if (mode === 'kline' && selectedSymbol) {
      setKlineLoading(true);
      setKlineError(null);
      void fetchKline(selectedSymbol);
    }
  }, [mode, selectedSymbol]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Quote fetching ---
  const fetchQuote = useCallback(async (symbol: string) => {
    setQuoteLoading(true);
    setQuoteError(null);
    try {
      const result = await quotesApi.getRealtime(symbol);
      if (result.items.length > 0) {
        setQuote(result.items[0]);
      } else {
        setQuote(null);
        setQuoteError(`未找到股票 ${symbol} 的实时行情数据`);
      }
    } catch {
      setQuote(null);
      setQuoteError('获取实时行情失败，请稍后重试');
    } finally {
      setQuoteLoading(false);
    }
  }, []);

  // --- K-line fetching ---
  const fetchKline = useCallback(async (symbol: string) => {
    setKlineLoading(true);
    setKlineError(null);
    try {
      const result = await klineApi.getKline(symbol);
      setKlineData(result);
    } catch {
      setKlineData(null);
      setKlineError('获取K线数据失败，请稍后重试');
    } finally {
      setKlineLoading(false);
    }
  }, []);

  // --- Handlers ---
  const handleStockSelect = useCallback((code: string) => {
    setSearchValue(code);
    setSearchParams({ symbol: code });
    void fetchQuote(code);
    // K-line will be fetched by the useEffect when mode is 'kline'
  }, [setSearchParams, fetchQuote]);

  return (
    <div className="flex h-[calc(100vh-2rem)] w-full flex-col gap-4">
      {/* Header */}
      <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Stock Analysis</p>
          <h1 className="text-2xl font-semibold text-slate-950">个股分析</h1>
          <p className="mt-0.5 text-sm text-slate-500">多维度股票数据分析与诊断</p>
        </div>
        <div className="flex gap-2 sm:w-auto">
          <div className="w-full sm:w-72">
            <StockAutocomplete
              value={searchValue}
              onChange={setSearchValue}
              onSubmit={handleStockSelect}
              placeholder="搜索股票代码或名称..."
              showSuggestionsOnFocus
            />
          </div>
          <div className="w-36 shrink-0">
            <Select
              value={mode}
              onChange={(v) => setMode(v as AnalysisMode)}
              options={[
                { value: 'realtime', label: '实时行情' },
                { value: 'kline', label: 'K线分析' },
              ]}
            />
          </div>
        </div>
      </div>

      {/* Main content */}
      {selectedSymbol ? (
        <main className="min-w-0 flex-1 overflow-y-auto">
          {mode === 'realtime' ? (
            quoteLoading ? (
              <div className="flex h-40 items-center justify-center">
                <div className="flex flex-col items-center gap-3">
                  <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                  <span className="text-sm text-slate-400">正在获取行情数据...</span>
                </div>
              </div>
            ) : quoteError && !quote ? (
              <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
                <p className="text-sm font-medium text-red-600">{quoteError}</p>
              </div>
            ) : quote ? (
              <RealtimeQuotePanel quote={quote} />
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                <p className="text-sm text-slate-400">暂无行情数据</p>
              </div>
            )
          ) : (
            <KLineChartPanel
              data={klineData}
              loading={klineLoading}
              error={klineError}
            />
          )}
        </main>
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-cyan-50">
              <Activity className="h-8 w-8 text-cyan-500" />
            </div>
            <h3 className="text-lg font-semibold text-slate-700">选择一只股票开始分析</h3>
            <p className="mt-2 text-sm text-slate-400">在上方搜索框中输入股票代码或名称</p>
          </div>
        </div>
      )}
    </div>
  );
};

export default StockAnalysisPage;
