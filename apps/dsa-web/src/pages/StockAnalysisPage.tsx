import React, { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSearchParams } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Building2,
  CheckCircle2,
  ChevronRight,
  Clock,
  DollarSign,
  FileText,
  Globe,
  Info,
  LoaderCircle,
  Percent,
  TrendingUp,
  Users,
  Zap,
} from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Select } from '../components/common';
import { useBusinessStream } from '../hooks';
import { quotesApi, type RealtimeQuote } from '../api/quotes';
import { klineApi, type KlineResponse } from '../api/kline';
import { stockInfoApi, type StockInfo } from '../api/stockInfo';
import { type BusinessResponse } from '../api/business';
import EnvironmentAnalysisCard from '../components/EnvironmentAnalysisCard';
import TrackQualityCard from '../components/TrackQualityCard';
import CatalystCard from '../components/CatalystCard';
import {
  announcementsApi,
  type AnnouncementsResponse,
  financialsApi,
  type FinancialsResponse,
  financialStatementsApi,
  type FinancialStatementsResponse,
  newsApi,
  type NewsResponse,
  riskEventsApi,
  type RiskEventsResponse,
  researchReportApi,
  type ResearchReportResponse,
  sentimentApi,
  type SentimentResponse,
  shareholderApi,
  type ShareholderStructureResponse,
  socialSentimentApi,
  type SocialSentimentResponse,
  valuationApi,
  type ValuationRatiosResponse,
} from '../api/financials';
import KLineChartPanel from '../components/KLineChartPanel';
import FinancialPanel from '../components/FinancialPanel';
import FinancialStatementsPanel from '../components/FinancialStatementsPanel';
import { AnnouncementsPanel, NewsPanel, ResearchPanel, RiskEventsPanel, SentimentPanel, SocialSentimentPanel } from '../components/NewsAnnouncementPanel';
import { BuyCriteriaPanel } from '../components/buyCriteria/BuyCriteriaPanel';
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
      <div className={cn('stock-analysis-hero p-6', changeBg)}>
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

      <div className="stock-analysis-panel">
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

      <div className="stock-analysis-panel">
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

      <div className="stock-analysis-panel">
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
      <div className="stock-analysis-panel">
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
        <div className="stock-analysis-panel">
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
        <div className="stock-analysis-panel">
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
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <FileText className="h-4 w-4 text-cyan-600" />主营业务
          </h3>
          <p className="text-sm leading-relaxed text-slate-600">{info.main_business}</p>
        </div>
      )}

      {/* 公司简介 */}
      {info.profile && (
        <div className="stock-analysis-panel">
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
// BusinessAnalysisPanel — 业务分析
// ---------------------------------------------------------------------------

function BusinessAnalysisPanel({ business }: { business: BusinessResponse }) {
  const [inputExpanded, setInputExpanded] = useState(false);
  const intro = business.intro;
  const composition = business.composition;

  // Get latest report date
  const latestReportDate = composition.length > 0
    ? composition.reduce((max, item) => item.report_date > max ? item.report_date : max, '')
    : '';

  // Filter by latest report date
  const latestComposition = composition.filter(item => item.report_date === latestReportDate);

  // Group by category type
  const byIndustry = latestComposition.filter(item => item.category_type === '按行业分类');
  const byProduct = latestComposition.filter(item => item.category_type === '按产品分类');
  const byRegion = latestComposition.filter(item => item.category_type === '按地区分类');

  return (
    <div className="space-y-6">
      {/* Catalyst Analysis */}
      {business.catalyst_analysis?.llm_used && (
        <CatalystCard analysis={business.catalyst_analysis} />
      )}

      {/* Track Quality */}
      {business.track_quality?.llm_used && (
        <TrackQualityCard analysis={business.track_quality} />
      )}

      {/* Environment Analysis */}
      {business.environment_analysis?.llm_used && (
        <EnvironmentAnalysisCard analysis={business.environment_analysis} />
      )}

      {/* LLM Business Analysis */}
      {business.llm_analysis?.llm_used && business.llm_analysis?.analysis && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Activity className="h-4 w-4 text-cyan-600" />业务动向分析
            {business.llm_analysis.model && (
              <span className="ml-auto text-xs font-normal text-slate-400">
                {business.llm_analysis.model.replace('openai/', '')}
              </span>
            )}
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-headings:text-slate-800 prose-headings:font-semibold
            prose-h3:text-sm prose-h3:mt-4 prose-h3:mb-2
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600
            prose-strong:text-slate-800 prose-strong:font-semibold
            prose-li:text-sm prose-li:text-slate-600
            prose-ul:space-y-1">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {business.llm_analysis.analysis}
            </ReactMarkdown>
          </div>
          {business.llm_analysis.llm_input && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              <button
                type="button"
                onClick={() => setInputExpanded(!inputExpanded)}
                className="flex w-full items-center gap-1.5 text-left text-xs text-slate-400 transition-colors hover:text-slate-600"
              >
                <ChevronRight className={cn('h-3 w-3 transition-transform', inputExpanded && 'rotate-90')} />
                分析输入数据
              </button>
              {inputExpanded && (
                <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-500 whitespace-pre-wrap">
                  {business.llm_analysis.llm_input}
                </pre>
              )}
            </div>
          )}
        </div>
      )}

      {/* Business Intro */}
      {(intro.main_business || intro.business_scope || intro.product_type || intro.product_name) && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <FileText className="h-4 w-4 text-cyan-600" />业务概况
          </h3>
          <div className="space-y-3">
            {intro.main_business && (
              <div>
                <p className="text-xs font-medium text-slate-400">主营业务</p>
                <p className="text-sm leading-relaxed text-slate-600">{intro.main_business}</p>
              </div>
            )}
            {intro.product_type && (
              <div>
                <p className="text-xs font-medium text-slate-400">产品类型</p>
                <p className="text-sm leading-relaxed text-slate-600">{intro.product_type}</p>
              </div>
            )}
            {intro.product_name && (
              <div>
                <p className="text-xs font-medium text-slate-400">产品名称</p>
                <p className="text-sm leading-relaxed text-slate-600">{intro.product_name}</p>
              </div>
            )}
            {intro.business_scope && (
              <div>
                <p className="text-xs font-medium text-slate-400">经营范围</p>
                <p className="text-sm leading-relaxed text-slate-600 line-clamp-4">{intro.business_scope}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Business Composition - By Industry */}
      {byIndustry.length > 0 && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />主营构成 — 按行业
            {latestReportDate && (
              <span className="ml-auto text-xs font-normal text-slate-400">{latestReportDate}</span>
            )}
          </h3>
          <div className="space-y-2">
            {byIndustry.map((item, index) => (
              <div key={`${item.business_name}-${index}`} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 border-b border-slate-50 pb-2 text-xs">
                <span className="font-medium text-slate-700">{item.business_name}</span>
                <span className="tabular-nums text-slate-600">
                  {item.revenue != null ? formatAmount(item.revenue) : '-'}
                </span>
                <span className="tabular-nums font-semibold text-slate-900">
                  {item.revenue_pct != null ? `${(item.revenue_pct * 100).toFixed(2)}%` : '-'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Business Composition - By Product */}
      {byProduct.length > 0 && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />主营构成 — 按产品
            {latestReportDate && (
              <span className="ml-auto text-xs font-normal text-slate-400">{latestReportDate}</span>
            )}
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[48rem] table-fixed">
              <colgroup>
                <col className="w-[30%]" />
                <col className="w-[18%]" />
                <col className="w-[18%]" />
                <col className="w-[18%]" />
                <col className="w-[16%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-100 text-xs text-slate-400">
                  <th className="pb-2 text-left font-medium">产品</th>
                  <th className="pb-2 text-right font-medium">收入</th>
                  <th className="pb-2 text-right font-medium">收入占比</th>
                  <th className="pb-2 text-right font-medium">毛利率</th>
                </tr>
              </thead>
              <tbody>
                {byProduct.map((item, index) => (
                  <tr key={`${item.business_name}-${index}`} className="border-b border-slate-50 text-xs">
                    <td className="py-2 pr-4 font-medium text-slate-700">{item.business_name}</td>
                    <td className="py-2 text-right tabular-nums text-slate-600">
                      {item.revenue != null ? formatAmount(item.revenue) : '-'}
                    </td>
                    <td className="py-2 text-right tabular-nums font-semibold text-slate-900">
                      {item.revenue_pct != null ? `${(item.revenue_pct * 100).toFixed(2)}%` : '-'}
                    </td>
                    <td className="py-2 text-right tabular-nums text-slate-600">
                      {item.gross_margin != null ? `${(item.gross_margin * 100).toFixed(2)}%` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Business Composition - By Region */}
      {byRegion.length > 0 && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />主营构成 — 按地区
            {latestReportDate && (
              <span className="ml-auto text-xs font-normal text-slate-400">{latestReportDate}</span>
            )}
          </h3>
          <div className="space-y-2">
            {byRegion.map((item, index) => (
              <div key={`${item.business_name}-${index}`} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 border-b border-slate-50 pb-2 text-xs">
                <span className="font-medium text-slate-700">{item.business_name}</span>
                <span className="tabular-nums text-slate-600">
                  {item.revenue != null ? formatAmount(item.revenue) : '-'}
                </span>
                <span className="tabular-nums font-semibold text-slate-900">
                  {item.revenue_pct != null ? `${(item.revenue_pct * 100).toFixed(2)}%` : '-'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

    </div>
  );
}

function BusinessAnalysisPanelStreaming({ symbol }: { symbol: string }) {
  const { phase, progressEvents, streamingText, envStreamingText, trackStreamingText, catalystStreamingText, business, isCached, error, startStream } = useBusinessStream();

  useEffect(() => {
    if (symbol) startStream(symbol);
  }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (phase === 'connecting' || phase === 'fetching') {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
          正在获取业务数据（含AI分析，请耐心等待）...
        </div>
        {progressEvents.length > 0 && (
          <div className="stock-analysis-panel">
            <div className="space-y-2">
              {progressEvents.map((p, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="text-slate-600">{p.label}</span>
                  <span className="ml-auto text-slate-400">{p.step}/{p.total}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (phase === 'analyzing' || (phase === 'done' && !business)) {
    // If business data already received (analysis_done) but further analysis still in progress
    if (business) {
      const catalystCard = catalystStreamingText ? (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Zap className="h-4 w-4 text-amber-500" />催化分析
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-amber-400" />
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{catalystStreamingText}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className="stock-analysis-panel">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-amber-500" />
            AI 正在分析催化因素...
          </div>
        </div>
      );

      const trackCard = trackStreamingText ? (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <TrendingUp className="h-4 w-4 text-cyan-600" />赛道质量评估
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-400" />
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{trackStreamingText}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className="stock-analysis-panel">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
            AI 正在评估赛道质量...
          </div>
        </div>
      );

      const envCard = envStreamingText ? (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Globe className="h-4 w-4 text-cyan-600" />外部环境分析
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-400" />
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{envStreamingText}</ReactMarkdown>
          </div>
        </div>
      ) : !trackStreamingText ? (
        <div className="stock-analysis-panel">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
            AI 正在分析外部环境...
          </div>
        </div>
      ) : null;

      return (
        <div className="space-y-6">
          {/* Catalyst analysis placeholder / streaming */}
          {catalystCard}
          {/* Track quality placeholder / streaming */}
          {trackCard}
          {/* Environment analysis placeholder / streaming */}
          {envCard}
          {/* Business LLM analysis (already completed) */}
          {business.llm_analysis?.llm_used && business.llm_analysis?.analysis && (
            <div className="stock-analysis-panel">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
                <Activity className="h-4 w-4 text-cyan-600" />业务动向分析
              </h3>
              <div className="prose prose-slate prose-sm max-w-none
                prose-headings:text-slate-800 prose-headings:font-semibold
                prose-h3:text-sm prose-h3:mt-4 prose-h3:mb-2
                prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600
                prose-strong:text-slate-800 prose-strong:font-semibold
                prose-li:text-sm prose-li:text-slate-600
                prose-ul:space-y-1">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {business.llm_analysis.analysis}
                </ReactMarkdown>
              </div>
            </div>
          )}
          {/* Remaining business panels */}
          <BusinessAnalysisPanel business={{ ...business, llm_analysis: { llm_used: false }, environment_analysis: undefined, track_quality: undefined, catalyst_analysis: undefined }} />
        </div>
      );
    }

    return (
      <div className="space-y-6">
        {progressEvents.length > 0 && (
          <div className="stock-analysis-panel">
            <div className="space-y-2">
              {progressEvents.map((p, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="text-slate-600">{p.label}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {streamingText && (
          <div className="stock-analysis-panel">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Activity className="h-4 w-4 text-cyan-600" />业务动向分析
              {phase === 'analyzing' && <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-400" />}
            </h3>
            <div className="prose prose-slate prose-sm max-w-none
              prose-headings:text-slate-800 prose-headings:font-semibold
              prose-h3:text-sm prose-h3:mt-4 prose-h3:mb-2
              prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600
              prose-strong:text-slate-800 prose-strong:font-semibold
              prose-li:text-sm prose-li:text-slate-600
              prose-ul:space-y-1">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {streamingText}
              </ReactMarkdown>
            </div>
          </div>
        )}
        {!streamingText && phase === 'analyzing' && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
            AI 正在分析业务动向...
          </div>
        )}
      </div>
    );
  }

  if (business) {
    return (
      <>
        <BusinessAnalysisPanel business={business} />
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
          <Clock className="h-3 w-3" />
          <span>
            数据获取时间: {business._fetched_at ? new Date(business._fetched_at).toLocaleString('zh-CN') : '-'}
            {isCached || business._cached ? ' · 缓存' : ' · 实时'}
          </span>
        </div>
      </>
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// Valuation + shareholder panels
// ---------------------------------------------------------------------------

function ValuationRatiosPanel({ valuation }: { valuation: ValuationRatiosResponse }) {
  const industry = valuation.industry_average;

  return (
    <div className="stock-analysis-panel">
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

function PriceOverdraftPanel({ valuation }: { valuation: ValuationRatiosResponse }) {
  const signal = valuation.price_overdraft_signal;
  if (!signal) return null;

  const statusMeta: Record<string, { label: string; tone: string; dot: string }> = {
    low: { label: '透支压力低', tone: 'text-emerald-700 bg-emerald-50 border-emerald-200', dot: 'bg-emerald-500' },
    watch: { label: '需要跟踪', tone: 'text-amber-700 bg-amber-50 border-amber-200', dot: 'bg-amber-500' },
    medium: { label: '中度透支', tone: 'text-orange-700 bg-orange-50 border-orange-200', dot: 'bg-orange-500' },
    high: { label: '明显透支', tone: 'text-red-700 bg-red-50 border-red-200', dot: 'bg-red-500' },
    uncertain: { label: '证据不足', tone: 'text-slate-700 bg-slate-50 border-slate-200', dot: 'bg-slate-400' },
  };
  const meta = statusMeta[signal.status] || statusMeta.uncertain;

  return (
    <div className="stock-analysis-panel">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <AlertTriangle className="h-4 w-4 text-cyan-600" />股价透支判定
        </h3>
        <span className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium', meta.tone)}>
          <span className={cn('h-2 w-2 rounded-full', meta.dot)} />
          {meta.label}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <DataItem label="透支风险分" value={formatRatio(signal.score)} highlight />
        <DataItem label="估值昂贵度" value={formatRatio(signal.valuation_expensive_score)} />
        <DataItem label="预期支撑度" value={formatRatio(signal.expectation_support_score)} />
        <DataItem label="证据完整度" value={formatPctValue(signal.confidence)} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 border-t border-slate-100 pt-4 md:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">关键判断依据</p>
          <div className="grid gap-2">
            {signal.reasoning.length > 0 ? signal.reasoning.map((item, index) => (
              <p key={`${index}-${item}`} className="text-sm leading-6 text-slate-600">{item}</p>
            )) : (
              <p className="text-sm text-slate-500">暂无判定说明</p>
            )}
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-slate-400">预期校准指标</p>
          <div className="grid grid-cols-2 gap-3">
            <DataItem label="PE相对行业" value={formatPctValue(signal.metrics.pe_premium_vs_industry)} />
            <DataItem label="PB相对行业" value={formatPctValue(signal.metrics.pb_premium_vs_industry)} />
            <DataItem label="动态PE改善" value={formatPctValue(signal.metrics.dynamic_pe_discount_vs_ttm)} />
            <DataItem label="PEG" value={formatRatio(signal.metrics.peg)} />
          </div>
        </div>
      </div>

      {(signal.signals.length > 0 || signal.limitations.length > 0) && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          {signal.signals.length > 0 && (
            <div className="mb-3">
              <p className="mb-2 text-xs font-medium text-slate-400">触发信号</p>
              <div className="flex flex-wrap gap-2">
                {signal.signals.map((item) => (
                  <span key={item} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
                    {item}
                  </span>
                ))}
              </div>
            </div>
          )}
          {signal.limitations.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium text-slate-400">判定边界</p>
              <div className="grid gap-1.5">
                {signal.limitations.map((item, index) => (
                  <p key={`${index}-${item}`} className="text-xs leading-5 text-slate-500">{item}</p>
                ))}
              </div>
            </div>
          )}
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
    <div className="stock-analysis-panel">
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

type AnalysisMode = 'overview' | 'kline' | 'financials' | 'business' | 'valuation' | 'industry-cycle' | 'shareholder' | 'news' | 'risk' | 'announcements' | 'sentiment' | 'research' | 'social';

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
  const [riskEvents, setRiskEvents] = useState<RiskEventsResponse | null>(null);
  const [riskEventsLoading, setRiskEventsLoading] = useState(false);

  // --- Announcements state ---
  const [announcements, setAnnouncements] = useState<AnnouncementsResponse | null>(null);
  const [announcementsLoading, setAnnouncementsLoading] = useState(false);

  // --- Sentiment state ---
  const [sentiment, setSentiment] = useState<SentimentResponse | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState(false);

  // --- Research state ---
  const [research, setResearch] = useState<ResearchReportResponse | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);

  // --- Social sentiment state ---
  const [social, setSocial] = useState<SocialSentimentResponse | null>(null);
  const [socialLoading, setSocialLoading] = useState(false);

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

  useEffect(() => {
    if (mode === 'risk' && selectedSymbol) {
      void fetchRiskEvents(selectedSymbol);
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

  // --- Fetch social sentiment when mode switches to 'social' ---
  useEffect(() => {
    if (mode === 'social' && selectedSymbol) {
      void fetchSocial(selectedSymbol);
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
      const result = await newsApi.searchNews(symbol, 90);
      setNews(result);
    } catch {
      setNews(null);
    } finally {
      setNewsLoading(false);
    }
  }, []);

  const fetchRiskEvents = useCallback(async (symbol: string) => {
    setRiskEventsLoading(true);
    try {
      const result = await riskEventsApi.getRiskEvents(symbol);
      setRiskEvents(result);
    } catch {
      setRiskEvents(null);
    } finally {
      setRiskEventsLoading(false);
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

  // --- Social sentiment fetching ---
  const fetchSocial = useCallback(async (symbol: string) => {
    setSocialLoading(true);
    try {
      const result = await socialSentimentApi.getSocialSentiment(symbol);
      setSocial(result);
    } catch {
      setSocial(null);
    } finally {
      setSocialLoading(false);
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
    <div className="stock-analysis-page flex min-h-full w-full flex-col gap-4">
      {/* Header */}
      <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Stock Analysis</p>
          <h1 className="text-2xl font-semibold text-slate-950">个股分析</h1>
          <p className="mt-0.5 text-sm text-slate-500">多维度股票数据分析与诊断</p>
        </div>
        <div className="stock-analysis-toolbar">
          <div className="min-w-0 flex-1 sm:flex-none sm:w-72">
            <StockAutocomplete
              value={searchValue}
              onChange={setSearchValue}
              onSubmit={handleStockSelect}
              placeholder="搜索股票代码或名称..."
              showSuggestionsOnFocus
              className="stock-analysis-input"
            />
          </div>
          <div className="w-[9.5rem] shrink-0 sm:w-36">
            <Select
              value={mode}
              onChange={(v) => setMode(v as AnalysisMode)}
              className="stock-analysis-select"
              options={[
                { value: 'overview', label: '行情概览' },
                { value: 'kline', label: 'K线分析' },
                { value: 'financials', label: '财报分析' },
                { value: 'business', label: '业务分析' },
                { value: 'valuation', label: '估值分析' },
                { value: 'industry-cycle', label: '买入判断' },
                { value: 'shareholder', label: '股东结构' },
                { value: 'news', label: '相关新闻' },
                { value: 'risk', label: '风险事件' },
                { value: 'announcements', label: '公司公告' },
                { value: 'sentiment', label: '舆情情绪' },
                { value: 'research', label: '券商研报' },
                { value: 'social', label: '社交情绪' },
              ]}
            />
          </div>
        </div>
      </div>

      {/* Main content */}
      {selectedSymbol ? (
        <main className="min-h-0 min-w-0 flex-1">
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
          ) : mode === 'business' ? (
            <div className="space-y-6">
              {selectedSymbol ? (
                <BusinessAnalysisPanelStreaming symbol={selectedSymbol} />
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">请选择一只股票</p>
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
                  <PriceOverdraftPanel valuation={valuation} />
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
          ) : mode === 'risk' ? (
            <div className="space-y-6">
              {riskEventsLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在扫描风险事件...</span>
                  </div>
                </div>
              ) : riskEvents ? (
                <>
                  <RiskEventsPanel riskEvents={riskEvents} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {riskEvents._fetched_at ? new Date(riskEvents._fetched_at).toLocaleString('zh-CN') : '-'}
                      {riskEvents._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                    <span className="text-slate-300">|</span>
                    <span>数据源: {formatSourceChain(riskEvents.source_chain)}</span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无风险事件</p>
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
          ) : mode === 'industry-cycle' ? (
            <BuyCriteriaPanel symbol={selectedSymbol} valuation={valuation ?? undefined} />
          ) : mode === 'social' ? (
            <div className="space-y-6">
              {socialLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                    <span className="text-sm text-slate-400">正在分析社交情绪...</span>
                  </div>
                </div>
              ) : social ? (
                <>
                  <SocialSentimentPanel social={social} />
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    <span>
                      数据获取时间: {social._fetched_at ? new Date(social._fetched_at).toLocaleString('zh-CN') : '-'}
                      {social._cached ? ' · 缓存' : ' · 实时'}
                    </span>
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
                  <p className="text-sm text-slate-400">暂无社交情绪数据</p>
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
