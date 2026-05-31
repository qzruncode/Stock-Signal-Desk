import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Building2,
  Clock,
  DollarSign,
  FileText,
  Info,
  Percent,
  TrendingUp,
  Users,
} from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Select } from '../components/common';
import { quotesApi, type RealtimeQuote } from '../api/quotes';
import { klineApi, type KlineResponse } from '../api/kline';
import { stockInfoApi, type StockInfo } from '../api/stockInfo';
import {
  announcementsApi,
  type AnnouncementsResponse,
  financialsApi,
  type FinancialsResponse,
  financialStatementsApi,
  type FinancialStatementsResponse,
  newsApi,
  type NewsResponse,
  researchReportApi,
  type ResearchReportResponse,
  sentimentApi,
  type SentimentResponse,
  shareholderApi,
  type ShareholderStructureResponse,
  valuationApi,
  type ValuationRatiosResponse,
} from '../api/financials';
import KLineChartPanel from '../components/KLineChartPanel';
import FinancialPanel from '../components/FinancialPanel';
import FinancialStatementsPanel from '../components/FinancialStatementsPanel';
import { AnnouncementsPanel, NewsPanel, ResearchPanel, SentimentPanel } from '../components/NewsAnnouncementPanel';
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

function formatShares(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿股`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万股`;
  return `${value.toFixed(0)}股`;
}

function formatRatio(value: number | null): string {
  return value == null ? '-' : value.toFixed(2);
}

function formatPctValue(value: number | null | undefined): string {
  return value == null ? '-' : `${value.toFixed(2)}%`;
}

function formatSourceChain(sources?: string[]): string {
  return sources?.filter(Boolean).join(' / ') || '-';
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

    </div>
  );
}

// ---------------------------------------------------------------------------
// StockInfoPanel — 公司概况
// ---------------------------------------------------------------------------

function StockInfoPanel({ info }: { info: StockInfo }) {
  const hasEm = info._em_ok === true;

  return (
    <div className="space-y-4">
      {/* 公司概况 */}
      <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Building2 className="h-4 w-4 text-cyan-600" />公司概况
        </h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <DataItem label="公司全称" value={info.name || info.short_name || '-'} />
          <DataItem label="所属行业" value={info.industry || '-'} />
          <DataItem label="所属市场" value={info.market || '-'} />
          <DataItem
            label="上市日期"
            value={info.listing_date ? info.listing_date.replace(/-/g, '/') : '-'}
          />
          <DataItem
            label="成立日期"
            value={info.establish_date ? info.establish_date.replace(/-/g, '/') : '-'}
          />
          <DataItem label="注册资本" value={formatAmount(info.register_capital)} />
        </div>
      </div>

      {/* 股本信息 */}
      {(info.total_shares != null || info.circ_shares != null) && (
        <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />股本信息
          </h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <DataItem label="总股本" value={formatShares(info.total_shares)} />
            <DataItem label="流通股本" value={formatShares(info.circ_shares)} />
            <DataItem label="总市值" value={formatMarketCap(info.total_mv)} />
            <DataItem label="流通市值" value={formatMarketCap(info.circ_mv)} />
          </div>
          {hasEm && (
            <p className="mt-2 text-xs text-slate-400">股本数据来源: 东方财富</p>
          )}
        </div>
      )}

      {/* 估值指标 (PE/PB) */}
      {(info.pe_dynamic != null || info.pe_static != null || info.pb_ratio != null) && (
        <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <TrendingUp className="h-4 w-4 text-cyan-600" />估值指标
          </h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <DataItem
              label="市盈率(动)"
              value={info.pe_dynamic != null ? info.pe_dynamic.toFixed(2) : '-'}
            />
            <DataItem
              label="市盈率(静)"
              value={info.pe_static != null ? info.pe_static.toFixed(2) : '-'}
            />
            <DataItem
              label="市净率"
              value={info.pb_ratio != null ? info.pb_ratio.toFixed(2) : '-'}
            />
          </div>
        </div>
      )}

      {/* 主营业务 */}
      {info.main_business && (
        <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <FileText className="h-4 w-4 text-cyan-600" />主营业务
          </h3>
          <p className="text-sm leading-relaxed text-slate-600">{info.main_business}</p>
        </div>
      )}

      {/* 公司简介 */}
      {info.profile && (
        <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Info className="h-4 w-4 text-cyan-600" />公司简介
          </h3>
          <p className="text-sm leading-relaxed text-slate-600 line-clamp-6">{info.profile}</p>
        </div>
      )}

    </div>
  );
}

// ---------------------------------------------------------------------------
// Valuation + shareholder panels
// ---------------------------------------------------------------------------

function ValuationRatiosPanel({ valuation }: { valuation: ValuationRatiosResponse }) {
  const industry = valuation.industry_average;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Percent className="h-4 w-4 text-cyan-600" />估值指标
        {valuation.trade_date && (
          <span className="ml-auto text-xs font-normal text-slate-400">{valuation.trade_date}</span>
        )}
      </h3>
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <DataItem label="PE(TTM)" value={formatRatio(valuation.pe_ttm)} highlight />
        <DataItem label="PE(动态)" value={formatRatio(valuation.pe_dynamic)} />
        <DataItem label="PE(静态)" value={formatRatio(valuation.pe_static)} />
        <DataItem label="PB" value={formatRatio(valuation.pb)} />
        <DataItem label="PS" value={formatRatio(valuation.ps)} />
        <DataItem label="PCF" value={formatRatio(valuation.pcf)} />
        <DataItem label="PEG" value={formatRatio(valuation.peg)} />
        <DataItem
          label={valuation.dividend_date ? `股息率(${valuation.dividend_date})` : '股息率'}
          value={formatPctValue(valuation.dividend_yield)}
        />
      </div>

      {(valuation.pe_percentiles?.['5y'] != null || industry?.industry) && (
        <div className="mt-4 grid grid-cols-1 gap-3 border-t border-slate-100 pt-4 md:grid-cols-2">
          <div>
            <p className="mb-2 text-xs font-medium text-slate-400">历史 PE 分位</p>
            <div className="grid grid-cols-3 gap-3">
              <DataItem label="近5年" value={formatPctValue(valuation.pe_percentiles?.['5y'])} />
              <DataItem label="近3年" value={formatPctValue(valuation.pe_percentiles?.['3y'])} />
              <DataItem label="近1年" value={formatPctValue(valuation.pe_percentiles?.['1y'])} />
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium text-slate-400">
              行业对比{industry?.industry ? ` · ${industry.industry}` : ''}
            </p>
            <div className="grid grid-cols-2 gap-3">
              <DataItem label="行业PE" value={formatRatio(industry?.pe ?? null)} />
              <DataItem label="行业PB" value={formatRatio(industry?.pb ?? null)} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ShareholderStructurePanel({ shareholder }: { shareholder: ShareholderStructureResponse }) {
  const countChange = shareholder.holder_count_change_pct ?? 0;
  const changeUp = countChange > 0;
  const changeDown = countChange < 0;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Users className="h-4 w-4 text-cyan-600" />股东结构
        {shareholder.holder_report_date && (
          <span className="ml-auto text-xs font-normal text-slate-400">{shareholder.holder_report_date}</span>
        )}
      </h3>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <DataItem label="股东人数" value={shareholder.holder_count != null ? shareholder.holder_count.toLocaleString('zh-CN') : '-'} highlight />
        <DataItem
          label="环比变化"
          value={formatPctValue(shareholder.holder_count_change_pct)}
          highlightUp={changeUp}
          highlightDown={changeDown}
        />
        <DataItem label="机构持股" value={formatPctValue(shareholder.institution_holding_pct)} />
        <DataItem label="实际控制人" value={shareholder.actual_controller || '-'} />
      </div>

      {shareholder.top10_holders.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] table-fixed">
              <colgroup>
                <col className="w-[48%]" />
                <col className="w-[14%]" />
                <col className="w-[20%]" />
                <col className="w-[18%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-100 text-xs text-slate-400">
                  <th className="pb-2 text-left font-medium">股东名称</th>
                  <th className="pb-2 text-right font-medium">持股比例</th>
                  <th className="border-r border-slate-100 pb-2 pr-6 text-right font-medium">持股数量</th>
                  <th className="pb-2 pl-6 text-left font-medium">性质</th>
                </tr>
              </thead>
              <tbody>
                {shareholder.top10_holders.slice(0, 10).map((holder, index) => (
                  <tr key={`${holder.name}-${index}`} className="border-b border-slate-50 text-xs">
                    <td className="py-2 pr-4 text-slate-700">{holder.name || '-'}</td>
                    <td className="py-2 text-right tabular-nums text-slate-700">{formatPctValue(holder.holding_pct)}</td>
                    <td className="border-r border-slate-100 py-2 pr-6 text-right tabular-nums text-slate-600">{formatShares(holder.holding_amount)}</td>
                    <td className="py-2 pl-6 text-slate-500">{holder.holder_type || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {shareholder.major_holder_changes.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="mb-2 text-xs font-medium text-slate-400">近期大股东增减持</p>
          <div className="grid gap-2">
            {shareholder.major_holder_changes.slice(0, 8).map((item, index) => (
              <div key={`${item.date}-${item.holder}-${index}`} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-3 text-xs">
                <span className="text-slate-400">{item.date || '-'}</span>
                <span className="truncate text-slate-700">{item.holder || '-'}</span>
                <span className="tabular-nums text-slate-500">
                  {item.direction || '-'} {formatShares(item.shares)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

type AnalysisMode = 'overview' | 'kline' | 'financials' | 'valuation' | 'shareholder' | 'news' | 'announcements' | 'sentiment' | 'research';

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

  // --- Stock info state ---
  const [stockInfo, setStockInfo] = useState<StockInfo | null>(null);
  const [stockInfoLoading, setStockInfoLoading] = useState(false);

  // --- Financials state ---
  const [financials, setFinancials] = useState<FinancialsResponse | null>(null);
  const [financialsLoading, setFinancialsLoading] = useState(false);

  // --- Financial statements state ---
  const [financialStatements, setFinancialStatements] = useState<FinancialStatementsResponse | null>(null);
  const [financialStatementsLoading, setFinancialStatementsLoading] = useState(false);

  // --- Valuation/shareholder state ---
  const [valuation, setValuation] = useState<ValuationRatiosResponse | null>(null);
  const [valuationLoading, setValuationLoading] = useState(false);
  const [shareholder, setShareholder] = useState<ShareholderStructureResponse | null>(null);
  const [shareholderLoading, setShareholderLoading] = useState(false);

  // --- News state ---
  const [news, setNews] = useState<NewsResponse | null>(null);
  const [newsLoading, setNewsLoading] = useState(false);

  // --- Announcements state ---
  const [announcements, setAnnouncements] = useState<AnnouncementsResponse | null>(null);
  const [announcementsLoading, setAnnouncementsLoading] = useState(false);

  // --- Sentiment state ---
  const [sentiment, setSentiment] = useState<SentimentResponse | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState(false);

  // --- Research state ---
  const [research, setResearch] = useState<ResearchReportResponse | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);

  // --- Tab mode ---
  const [mode, setMode] = useState<AnalysisMode>('overview');

  const selectedSymbol = searchParams.get('symbol');

  // Sync search value from URL (only fetch realtime on initial load)
  useEffect(() => {
    const symbol = searchParams.get('symbol');
    if (symbol) {
      setSearchValue(symbol);
      void fetchQuote(symbol);
      void fetchStockInfo(symbol);
      void fetchFinancials(symbol);
      void fetchFinancialStatements(symbol);
      void fetchValuation(symbol);
      void fetchShareholder(symbol);
      // K-line, news, announcements are fetched lazily when user switches to those tabs
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

  // --- Fetch news when mode switches to 'news' ---
  useEffect(() => {
    if (mode === 'news' && selectedSymbol) {
      void fetchNews(selectedSymbol);
    }
  }, [mode, selectedSymbol]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Fetch announcements when mode switches to 'announcements' ---
  useEffect(() => {
    if (mode === 'announcements' && selectedSymbol) {
      void fetchAnnouncements(selectedSymbol);
    }
  }, [mode, selectedSymbol]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Fetch sentiment when mode switches to 'sentiment' ---
  useEffect(() => {
    if (mode === 'sentiment' && selectedSymbol) {
      void fetchSentiment(selectedSymbol);
    }
  }, [mode, selectedSymbol]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Fetch research when mode switches to 'research' ---
  useEffect(() => {
    if (mode === 'research' && selectedSymbol) {
      void fetchResearch(selectedSymbol);
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

  // --- Stock info fetching ---
  const fetchStockInfo = useCallback(async (symbol: string) => {
    setStockInfoLoading(true);
    try {
      const result = await stockInfoApi.getInfo(symbol);
      setStockInfo(result);
    } catch {
      setStockInfo(null);
    } finally {
      setStockInfoLoading(false);
    }
  }, []);

  // --- Financials fetching ---
  const fetchFinancials = useCallback(async (symbol: string) => {
    setFinancialsLoading(true);
    try {
      const result = await financialsApi.getFinancials(symbol);
      setFinancials(result);
    } catch {
      setFinancials(null);
    } finally {
      setFinancialsLoading(false);
    }
  }, []);

  // --- Financial statements fetching ---
  const fetchFinancialStatements = useCallback(async (symbol: string) => {
    setFinancialStatementsLoading(true);
    try {
      const result = await financialStatementsApi.getStatements(symbol);
      setFinancialStatements(result);
    } catch {
      setFinancialStatements(null);
    } finally {
      setFinancialStatementsLoading(false);
    }
  }, []);

  // --- Valuation fetching ---
  const fetchValuation = useCallback(async (symbol: string) => {
    setValuationLoading(true);
    try {
      const result = await valuationApi.getValuationRatios(symbol);
      setValuation(result);
    } catch {
      setValuation(null);
    } finally {
      setValuationLoading(false);
    }
  }, []);

  // --- Shareholder fetching ---
  const fetchShareholder = useCallback(async (symbol: string) => {
    setShareholderLoading(true);
    try {
      const result = await shareholderApi.getShareholderStructure(symbol);
      setShareholder(result);
    } catch {
      setShareholder(null);
    } finally {
      setShareholderLoading(false);
    }
  }, []);

  // --- News fetching ---
  const fetchNews = useCallback(async (symbol: string) => {
    setNewsLoading(true);
    try {
      const result = await newsApi.searchNews(symbol);
      setNews(result);
    } catch {
      setNews(null);
    } finally {
      setNewsLoading(false);
    }
  }, []);

  // --- Announcements fetching ---
  const fetchAnnouncements = useCallback(async (symbol: string) => {
    setAnnouncementsLoading(true);
    try {
      const result = await announcementsApi.getAnnouncements(symbol);
      setAnnouncements(result);
    } catch {
      setAnnouncements(null);
    } finally {
      setAnnouncementsLoading(false);
    }
  }, []);

  // --- Sentiment fetching ---
  const fetchSentiment = useCallback(async (symbol: string) => {
    setSentimentLoading(true);
    try {
      const result = await sentimentApi.getSentiment(symbol);
      setSentiment(result);
    } catch {
      setSentiment(null);
    } finally {
      setSentimentLoading(false);
    }
  }, []);

  // --- Research fetching ---
  const fetchResearch = useCallback(async (symbol: string) => {
    setResearchLoading(true);
    try {
      const result = await researchReportApi.getResearchReports(symbol);
      setResearch(result);
    } catch {
      setResearch(null);
    } finally {
      setResearchLoading(false);
    }
  }, []);

  // --- Handlers ---
  const handleStockSelect = useCallback((code: string) => {
    setSearchValue(code);
    setSearchParams({ symbol: code });
    void fetchQuote(code);
    void fetchStockInfo(code);
    void fetchFinancials(code);
    void fetchFinancialStatements(code);
    void fetchValuation(code);
    void fetchShareholder(code);
    // K-line, news, announcements will be fetched by useEffect when mode is selected
  }, [setSearchParams, fetchQuote, fetchStockInfo, fetchFinancials, fetchFinancialStatements, fetchValuation, fetchShareholder]);

  return (
    <div className="flex h-[calc(100vh-2rem)] w-full flex-col gap-4 overflow-hidden">
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
                { value: 'overview', label: '行情概览' },
                { value: 'kline', label: 'K线分析' },
                { value: 'financials', label: '财报分析' },
                { value: 'valuation', label: '估值分析' },
                { value: 'shareholder', label: '股东结构' },
                { value: 'news', label: '相关新闻' },
                { value: 'announcements', label: '公司公告' },
                { value: 'sentiment', label: '舆情情绪' },
                { value: 'research', label: '券商研报' },
              ]}
            />
          </div>
        </div>
      </div>

      {/* Main content */}
      {selectedSymbol ? (
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
          {mode === 'overview' ? (
            <div className="space-y-6">
              {quoteLoading ? (
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
              )}

              {/* Financial panel — core data, shown right after quote */}
              {quote && (
                financialsLoading ? (
                  <div className="flex h-20 items-center justify-center">
                    <div className="flex flex-col items-center gap-2">
                      <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                      <span className="text-xs text-slate-400">正在获取财务数据...</span>
                    </div>
                  </div>
                ) : (
                  <FinancialPanel items={financials?.items ?? []} />
                )
              )}

              {/* Stock info panel — shown when quote is available */}
              {quote && (
                stockInfoLoading ? (
                  <div className="flex h-20 items-center justify-center">
                    <div className="flex flex-col items-center gap-2">
                      <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                      <span className="text-xs text-slate-400">正在获取公司资料...</span>
                    </div>
                  </div>
                ) : stockInfo ? (
                  <StockInfoPanel info={stockInfo} />
                ) : null
              )}

              {/* Shared footer */}
              {quote && (
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
              )}
            </div>
          ) : mode === 'kline' ? (
            <KLineChartPanel
              data={klineData}
              loading={klineLoading}
              error={klineError}
            />
          ) : mode === 'financials' ? (
            <div className="space-y-6">
              {financialStatementsLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在获取财报数据...</span>
                  </div>
                </div>
              ) : financialStatements ? (
                <FinancialStatementsPanel
                  balance_sheet={financialStatements.balance_sheet}
                  income_statement={financialStatements.income_statement}
                  cashflow={financialStatements.cashflow}
                />
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无财报数据</p>
                </div>
              )}
              {financialStatements && (
                <div className="flex items-center gap-1.5 text-xs text-slate-400">
                  <Clock className="h-3 w-3" />
                  <span>
                    数据获取时间: {financialStatements._fetched_at ? new Date(financialStatements._fetched_at).toLocaleString('zh-CN') : '-'}
                    {financialStatements._cached ? ' · 缓存' : ' · 实时'}
                  </span>
                  {financialStatements.source && (
                    <>
                      <span className="text-slate-300">|</span>
                      <span>数据源: {financialStatements.source}</span>
                    </>
                  )}
                </div>
              )}
            </div>
          ) : mode === 'valuation' ? (
            <div className="space-y-6">
              {valuationLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在获取估值指标...</span>
                  </div>
                </div>
              ) : valuation ? (
                <>
                  <ValuationRatiosPanel valuation={valuation} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {valuation._fetched_at ? new Date(valuation._fetched_at).toLocaleString('zh-CN') : '-'}
                      {valuation._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                    <span className="text-slate-300">|</span>
                    <span>数据源: {formatSourceChain(valuation.source_chain)}</span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无估值数据</p>
                </div>
              )}
            </div>
          ) : mode === 'news' ? (
            <div className="space-y-6">
              {newsLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在获取相关新闻...</span>
                  </div>
                </div>
              ) : news ? (
                <>
                  <NewsPanel news={news} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {news._fetched_at ? new Date(news._fetched_at).toLocaleString('zh-CN') : '-'}
                      {news._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                    <span className="text-slate-300">|</span>
                    <span>数据源: {formatSourceChain(news.source_chain)}</span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无相关新闻</p>
                </div>
              )}
            </div>
          ) : mode === 'announcements' ? (
            <div className="space-y-6">
              {announcementsLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在获取公司公告...</span>
                  </div>
                </div>
              ) : announcements ? (
                <>
                  <AnnouncementsPanel announcements={announcements} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {announcements._fetched_at ? new Date(announcements._fetched_at).toLocaleString('zh-CN') : '-'}
                      {announcements._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无公司公告</p>
                </div>
              )}
            </div>
          ) : mode === 'sentiment' ? (
            <div className="space-y-6">
              {sentimentLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在分析舆情情绪...</span>
                  </div>
                </div>
              ) : sentiment ? (
                <>
                  <SentimentPanel sentiment={sentiment} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {sentiment._fetched_at ? new Date(sentiment._fetched_at).toLocaleString('zh-CN') : '-'}
                      {sentiment._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无舆情数据</p>
                </div>
              )}
            </div>
          ) : mode === 'research' ? (
            <div className="space-y-6">
              {researchLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在获取券商研报...</span>
                  </div>
                </div>
              ) : research ? (
                <>
                  <ResearchPanel research={research} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {research._fetched_at ? new Date(research._fetched_at).toLocaleString('zh-CN') : '-'}
                      {research._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无券商研报</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-6">
              {shareholderLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在获取股东结构...</span>
                  </div>
                </div>
              ) : shareholder ? (
                <>
                  <ShareholderStructurePanel shareholder={shareholder} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {shareholder._fetched_at ? new Date(shareholder._fetched_at).toLocaleString('zh-CN') : '-'}
                      {shareholder._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                    <span className="text-slate-300">|</span>
                    <span>数据源: {formatSourceChain(shareholder.source_chain)}</span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无股东结构数据</p>
                </div>
              )}
            </div>
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
