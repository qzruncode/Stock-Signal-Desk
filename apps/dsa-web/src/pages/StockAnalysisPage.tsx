import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Building2,
  ChevronDown,
  Clock,
  DollarSign,
  FileText,
  Info,
  Percent,
  TrendingUp,
  Users,
} from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Badge, Button, InlineAlert, Select } from '../components/common';
import { quotesApi, type RealtimeQuote } from '../api/quotes';
import { klineApi, type KlineResponse } from '../api/kline';
import { stockInfoApi, type StockInfo } from '../api/stockInfo';
import { analysisApi } from '../api/analysis';
import {
  announcementsApi,
  type AnnouncementsResponse,
  financialsApi,
  type FinancialsResponse,
  financialStatementsApi,
  type FinancialStatementsResponse,
  industryCycleApi,
  type IndustryCycleReportTaskAccepted,
  type IndustryCycleResponse,
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
import { useTaskStream } from '../hooks/useTaskStream';
import KLineChartPanel from '../components/KLineChartPanel';
import FinancialPanel from '../components/FinancialPanel';
import FinancialStatementsPanel from '../components/FinancialStatementsPanel';
import { AnnouncementsPanel, NewsPanel, ResearchPanel, RiskEventsPanel, SentimentPanel, SocialSentimentPanel } from '../components/NewsAnnouncementPanel';
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

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? '');
  }
}

function pickDebugInputField<T = unknown>(
  debugInput: IndustryCycleResponse['debug_input'] | null | undefined,
  snakeKey: 'system_prompt' | 'user_prompt' | 'evidence_pack',
  camelKey: 'systemPrompt' | 'userPrompt' | 'evidencePack',
): T | undefined {
  if (!debugInput || typeof debugInput !== 'object') {
    return undefined;
  }
  const value = debugInput as Record<string, unknown>;
  return (value[snakeKey] as T | undefined) ?? (value[camelKey] as T | undefined);
}

function detectStreamKind(text: string): 'json' | 'report' {
  const trimmed = text.trim();
  return trimmed.startsWith('{') || trimmed.startsWith('[') ? 'json' : 'report';
}

function formatStreamText(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return '';
  if (detectStreamKind(trimmed) !== 'json') return trimmed;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return trimmed;
  }
}

function renderParagraphs(text: string): React.ReactNode {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
  if (paragraphs.length === 0) return null;
  return (
    <div className="space-y-4">
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 16)}`} className="text-sm leading-7 text-slate-700">
          {paragraph}
        </p>
      ))}
    </div>
  );
}

function formatEvidenceSectionLabel(key: string): string {
  const labels: Record<string, string> = {
    analysis_framework: '判定框架',
    market_mainline_report: '市场主线报告',
    market_mainline_evidence: '市场主线证据',
    stock_focus_snapshot: '个股聚焦摘要',
    stock_profile: '公司资料',
    mainline_context: '主线上下文',
    company_specific_evidence: '个股直接证据',
    industry_beta_evidence: '行业 Beta 证据',
    supporting_judgement: '辅助判断',
  };
  if (labels[key]) return labels[key];
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function isEvidenceNoiseString(value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  return [
    'HTTPConnectionPool(',
    'Read timed out',
    'RemoteDisconnected',
    '请求失败',
    '服务降级',
    '暂时不可用',
  ].some((token) => text.includes(token));
}

function sanitizeEvidenceValue(value: unknown): unknown {
  if (value == null) return null;
  if (typeof value === 'string') {
    const text = value.trim();
    if (!text) return null;
    if (isEvidenceNoiseString(text)) return null;
    return text;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  if (Array.isArray(value)) {
    const cleaned = value
      .map((item) => sanitizeEvidenceValue(item))
      .filter((item) => item != null && (!(Array.isArray(item)) || item.length > 0) && (!(typeof item === 'object' && item !== null) || Object.keys(item as Record<string, unknown>).length > 0));
    return cleaned.length > 0 ? cleaned : null;
  }
  if (typeof value === 'object') {
    const source = value as Record<string, unknown>;
    const omitKeys = new Set([
      '_cached',
      '_fetched_at',
      'errors',
      'error',
      'source_chain',
      'news_errors',
      'research_errors',
      'social_errors',
      'news_source_chain',
      'research_source_chain',
      'report_pending',
    ]);
    const cleanedEntries = Object.entries(source)
      .filter(([key]) => !omitKeys.has(key))
      .map(([key, item]) => [key, sanitizeEvidenceValue(item)] as const)
      .filter(([, item]) => item != null && (!(Array.isArray(item)) || item.length > 0) && (!(typeof item === 'object' && item !== null) || Object.keys(item as Record<string, unknown>).length > 0));
    if (cleanedEntries.length === 0) return null;
    return Object.fromEntries(cleanedEntries);
  }
  return null;
}

function sanitizePromptText(text: string | undefined): string {
  if (!text) return '-';
  return text
    .replace(/HTTPConnectionPool\([^\n]+/g, '[已省略数据源请求错误详情]')
    .replace(/Read timed out\.?/g, '[已省略超时详情]')
    .replace(/请求失败[:：][^\n"]+/g, '请求失败：[已省略详情]')
    .replace(/RemoteDisconnected[^\n"]*/g, '[已省略连接中断详情]');
}

function shouldSuppressEvidenceSection(key: string, value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  if (key === 'market_mainline_evidence' || key === 'mainline_context' || key === 'market_mainline_report') {
    const current = Array.isArray(record.currentThemes) ? record.currentThemes : Array.isArray(record.current_themes) ? record.current_themes : Array.isArray(record.current_mainlines) ? record.current_mainlines : [];
    const next = Array.isArray(record.nextThemes) ? record.nextThemes : Array.isArray(record.next_themes) ? record.next_themes : Array.isArray(record.future_mainlines) ? record.future_mainlines : [];
    const policy = Array.isArray(record.policyWatchlist) ? record.policyWatchlist : Array.isArray(record.policy_watchlist) ? record.policy_watchlist : [];
    const marketStage = record.marketStage ?? record.market_stage;
    const description = typeof marketStage === 'object' && marketStage
      ? String((marketStage as Record<string, unknown>).description || (marketStage as Record<string, unknown>).label || '')
      : '';
    if (current.length === 0 && next.length === 0 && policy.length === 0 && isEvidenceNoiseString(description)) {
      return true;
    }
  }
  return false;
}

function isScalarLike(value: unknown): boolean {
  return ['string', 'number', 'boolean'].includes(typeof value);
}

function renderEvidencePackCards(evidencePack: Record<string, unknown> | undefined): React.ReactNode {
  const cleanedEvidencePack = sanitizeEvidenceValue(evidencePack) as Record<string, unknown> | null;
  if (!cleanedEvidencePack || Object.keys(cleanedEvidencePack).length === 0) {
    return <span className="text-sm text-slate-400">暂无证据输入</span>;
  }

  const sectionOrder = [
    'stock_focus_snapshot',
    'stock_profile',
    'company_specific_evidence',
    'industry_beta_evidence',
    'mainline_context',
    'supporting_judgement',
    'analysis_framework',
    'market_mainline_report',
    'market_mainline_evidence',
  ];
  const sections = Object.entries(cleanedEvidencePack)
    .filter(([key, value]) => !['symbol', 'stock_name', 'industry_name', 'generated_at'].includes(key) && !shouldSuppressEvidenceSection(key, value))
    .sort(([left], [right]) => {
      const leftIndex = sectionOrder.indexOf(left);
      const rightIndex = sectionOrder.indexOf(right);
      return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex);
    });

  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-2">
        {sections.map(([key, value]) => (
          <div key={key} className="min-w-0 overflow-hidden market-mainline-muted-block">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h4 className="min-w-0 break-words text-sm font-semibold text-slate-900">{formatEvidenceSectionLabel(key)}</h4>
              <span className="max-w-[40%] truncate rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500">{key}</span>
            </div>
            {isScalarLike(value) ? (
              <div className="rounded-[1.25rem] border border-cyan-100 bg-white px-4 py-3 text-sm leading-7 text-slate-700">
                {String(value)}
              </div>
            ) : (
              <div className="min-w-0 market-stream-panel market-stream-json max-h-[22rem] overflow-auto rounded-[1.25rem]">
                <pre className="market-stream-pre whitespace-pre-wrap break-words">{prettyJson(value)}</pre>
              </div>
            )}
          </div>
        ))}
      </div>

      <details className="market-mainline-muted-block">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">查看完整原始 JSON</summary>
        <div className="mt-4">
          <div className="min-w-0 market-stream-panel market-stream-json max-h-[28rem] overflow-auto rounded-[1.25rem]">
            <pre className="market-stream-pre whitespace-pre-wrap break-words">{prettyJson(cleanedEvidencePack)}</pre>
          </div>
        </div>
      </details>
    </div>
  );
}

function readStockFocusSnapshot(
  displayData: IndustryCycleResponse | IndustryCycleDraft | null,
  evidencePack?: Record<string, unknown>,
): Record<string, unknown> | null {
  const fromDisplay = (displayData?.industry_cycle?.evidence as Record<string, unknown> | undefined)?.stock_focus_snapshot;
  if (fromDisplay && typeof fromDisplay === 'object') {
    return fromDisplay as Record<string, unknown>;
  }

  const topLevel = evidencePack?.stock_focus_snapshot;
  if (topLevel && typeof topLevel === 'object') {
    return topLevel as Record<string, unknown>;
  }

  const companySpecific = evidencePack?.company_specific_evidence;
  if (companySpecific && typeof companySpecific === 'object') {
    const nested = (companySpecific as Record<string, unknown>).stock_focus_snapshot;
    if (nested && typeof nested === 'object') {
      return nested as Record<string, unknown>;
    }
  }

  return null;
}

function renderStringList(items: string[] | undefined, emptyText: string): React.ReactNode {
  if (!items || items.length === 0) {
    return <p className="text-sm text-slate-400">{emptyText}</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span key={item} className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600">
          {item}
        </span>
      ))}
    </div>
  );
}

function renderDetectorChecklist(
  title: string,
  detector: IndustryCycleResponse['industry_cycle']['mainline_detector'] | Partial<IndustryCycleResponse['industry_cycle']['mainline_detector']> | undefined,
): React.ReactNode {
  const checklist = Array.isArray(detector?.checklist) ? detector.checklist : [];
  return (
    <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        <Badge variant={detector?.passed ? 'success' : 'default'}>
          {detector?.passed ? '通过' : '待确认'}
        </Badge>
      </div>
      {detector?.conclusion ? (
        <p className="mb-2 text-sm leading-6 text-slate-600">{detector.conclusion}</p>
      ) : null}
      {detector?.failed_reason ? (
        <p className="mb-2 text-sm leading-6 text-amber-700">{detector.failed_reason}</p>
      ) : null}
      {checklist.length > 0 ? (
        <div className="space-y-1.5">
          {checklist.map((item, index) => (
            <div key={`${item.item}-${index}`} className="rounded-xl bg-slate-50/80 px-3 py-2.5">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-medium text-slate-900">{item.item}</p>
                <span className={cn(
                  'rounded-full px-2.5 py-0.5 text-xs font-medium',
                  item.passed ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500',
                )}>
                  {item.passed ? '通过' : '未过'}
                </span>
              </div>
              {item.reason ? <p className="mt-2 text-sm leading-6 text-slate-600">{item.reason}</p> : null}
              {item.source ? <p className="mt-1 text-xs text-slate-400">{item.source}</p> : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-400">模型正在补充判定器明细…</p>
      )}
    </div>
  );
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

type IndustryCycleTaskPayload = {
  phase?: string;
  stream_text?: string;
  report_draft?: IndustryCycleDraft;
  report?: IndustryCycleResponse;
  debug_input?: IndustryCycleResponse['debug_input'];
};

type IndustryCycleDraft = Partial<Omit<IndustryCycleResponse, 'industry_cycle'>> & {
  industry_cycle?: Partial<IndustryCycleResponse['industry_cycle']> | null;
};

function mergeIndustryCycleTaskOverlay(
  current: IndustryCycleResponse | null,
  payload: IndustryCycleTaskPayload,
): IndustryCycleResponse | null {
  if (payload.report) {
    return payload.report;
  }
  if (!current) {
    return null;
  }
  return {
    symbol: current.symbol,
    industry_cycle: current.industry_cycle,
    report_pending: true,
    llm_used: current.llm_used ?? false,
    model_used: current.model_used ?? null,
    raw_stream_output: payload.stream_text || current.raw_stream_output || '',
    raw_response: payload.stream_text || current.raw_response || '',
    debug_input: payload.debug_input || current.debug_input || null,
    _fetched_at: current._fetched_at,
    _cached: false,
    fallback_used: current.fallback_used ?? false,
  };
}

function isIndustryCycleResponseLike(value: unknown): value is IndustryCycleResponse {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const payload = value as Record<string, unknown>;
  return 'industry_cycle' in payload || 'report_pending' in payload || 'raw_stream_output' in payload;
}

function readIndustryCycleTaskPayload(value: unknown): IndustryCycleTaskPayload {
  if (!value || typeof value !== 'object') {
    return {};
  }
  const payload = value as Record<string, unknown>;
  const nested = payload.report && typeof payload.report === 'object'
    ? readIndustryCycleTaskPayload(payload.report)
    : {};
  return {
    phase: (payload.phase as string | undefined) ?? nested.phase ?? undefined,
    stream_text:
      (payload.stream_text as string | undefined)
      ?? (payload.streamText as string | undefined)
      ?? nested.stream_text
      ?? undefined,
    report_draft:
      (payload.report_draft as IndustryCycleDraft | undefined)
      ?? (payload.reportDraft as IndustryCycleDraft | undefined)
      ?? nested.report_draft
      ?? undefined,
    report:
      (isIndustryCycleResponseLike(payload.report) ? payload.report : undefined)
      ?? nested.report
      ?? undefined,
    debug_input:
      ((payload.debug_input as IndustryCycleResponse['debug_input']) ?? (payload.debugInput as IndustryCycleResponse['debug_input']))
      ?? nested.debug_input
      ?? undefined,
  };
}

function mergeIndustryCycleDraft(
  previous: IndustryCycleDraft | null,
  nextDraft?: IndustryCycleDraft | null,
): IndustryCycleDraft | null {
  if (!nextDraft) {
    return previous;
  }
  if (!previous) {
    return nextDraft;
  }

  const previousStream = previous.raw_stream_output || previous.raw_response || '';
  const nextStream = nextDraft.raw_stream_output || nextDraft.raw_response || '';
  const previousCycle = previous.industry_cycle ?? null;
  const nextCycle = nextDraft.industry_cycle ?? null;
  const previousEvidence = previousCycle?.evidence;
  const nextEvidence = nextCycle?.evidence;
  const mergedCycle = (previousCycle || nextCycle ? {
    ...(previousCycle ?? {}),
    ...(nextCycle ?? {}),
    evidence: previousEvidence || nextEvidence ? {
      ...(previousEvidence ?? {}),
      ...(nextEvidence ?? {}),
      market_mainline: nextEvidence?.market_mainline ?? previousEvidence?.market_mainline,
      sector_snapshot: nextEvidence?.sector_snapshot ?? previousEvidence?.sector_snapshot,
      fund_flow: nextEvidence?.fund_flow ?? previousEvidence?.fund_flow,
      peer_group: nextEvidence?.peer_group ?? previousEvidence?.peer_group,
      valuation_snapshot: nextEvidence?.valuation_snapshot ?? previousEvidence?.valuation_snapshot,
      sentiment_snapshot: nextEvidence?.sentiment_snapshot ?? previousEvidence?.sentiment_snapshot,
      risk_snapshot: nextEvidence?.risk_snapshot ?? previousEvidence?.risk_snapshot,
      data_quality: nextEvidence?.data_quality ?? previousEvidence?.data_quality,
      financial_snapshot: nextEvidence?.financial_snapshot ?? previousEvidence?.financial_snapshot,
      driver_signals: nextEvidence?.driver_signals ?? previousEvidence?.driver_signals,
    } : undefined,
    catalysts: nextCycle?.catalysts ?? previousCycle?.catalysts,
    risks: nextCycle?.risks ?? previousCycle?.risks,
    observation_points: nextCycle?.observation_points ?? previousCycle?.observation_points,
    mainline_detector: nextCycle?.mainline_detector ?? previousCycle?.mainline_detector,
    industry_beta_detector: nextCycle?.industry_beta_detector ?? previousCycle?.industry_beta_detector,
  } : null) as IndustryCycleDraft['industry_cycle'];

  return {
    ...previous,
    ...nextDraft,
    raw_stream_output: nextStream.length >= previousStream.length ? (nextDraft.raw_stream_output || nextDraft.raw_response) : previous.raw_stream_output,
    raw_response: nextStream.length >= previousStream.length ? (nextDraft.raw_response || nextDraft.raw_stream_output) : previous.raw_response,
    debug_input: nextDraft.debug_input ?? previous.debug_input,
    industry_cycle: mergedCycle,
  };
}

function IndustryCyclePanel({
  data,
  draft,
  debugInput,
  loading,
  isGenerating,
  streamText,
  streamPhase,
  taskMessage,
  taskError,
  onRegenerate,
}: {
  data: IndustryCycleResponse | null;
  draft: IndustryCycleDraft | null;
  debugInput: IndustryCycleResponse['debug_input'];
  loading: boolean;
  isGenerating: boolean;
  streamText: string;
  streamPhase: string | null;
  taskMessage: string | null;
  taskError: string | null;
  onRegenerate: () => void;
}) {
  const [showRawInput, setShowRawInput] = useState(true);
  const [showRawOutput, setShowRawOutput] = useState(false);
  const displayData = mergeIndustryCycleDraft(data, draft) as IndustryCycleResponse | IndustryCycleDraft | null;
  const activeDebugInput = debugInput || draft?.debug_input || displayData?.debug_input || null;
  const activeEvidencePack = pickDebugInputField<Record<string, unknown>>(activeDebugInput, 'evidence_pack', 'evidencePack');
  const stockFocusSnapshot = readStockFocusSnapshot(displayData, activeEvidencePack);
  const stockFocusView = typeof stockFocusSnapshot?.focus_view === 'string' ? stockFocusSnapshot.focus_view : '';
  const businessBindingStrength = typeof stockFocusSnapshot?.business_binding_strength === 'string' ? stockFocusSnapshot.business_binding_strength : '';
  const financeState = typeof stockFocusSnapshot?.finance_state === 'string' ? stockFocusSnapshot.finance_state : '';
  const holderState = typeof stockFocusSnapshot?.holder_state === 'string' ? stockFocusSnapshot.holder_state : '';
  const tradingState = typeof stockFocusSnapshot?.trading_state === 'string' ? stockFocusSnapshot.trading_state : '';
  const directEvidenceStrength = typeof stockFocusSnapshot?.direct_evidence_strength === 'string' ? stockFocusSnapshot.direct_evidence_strength : '';

  if (loading && !displayData && !isGenerating) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">正在加载行业周期报告...</span>
        </div>
      </div>
    );
  }

  const displayText = String(
    isGenerating
      ? streamText
      : (displayData?.raw_stream_output || displayData?.raw_response || streamText || ''),
  ).trim();
  const streamKind = detectStreamKind(displayText);
  const formattedDisplayText = formatStreamText(displayText);
  const streamStatsText = formattedDisplayText ? `${formattedDisplayText.length.toLocaleString()} 字符` : '等待首个分片';
  const cycle = displayData?.industry_cycle;
  const streamContent = cycle ? (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-slate-950 px-3 py-1 text-xs font-semibold text-white">
          {cycle.analysis_status || '观察'}
        </span>
        <span className="rounded-full bg-cyan-50 px-3 py-1 text-xs font-medium text-cyan-700">
          受益级别 {cycle.beneficiary_level || '待模型确认'}
        </span>
        <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">
          周期阶段 {cycle.cycle_phase || '待模型确认'}
        </span>
        <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600">
          景气分 {typeof cycle.prosperity_score === 'number' ? `${cycle.prosperity_score}/10` : '待模型确认'}
        </span>
      </div>

      <div className="grid gap-2 lg:grid-cols-2">
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">受益路径</p>
          <p className="text-sm leading-6 text-slate-700">{cycle.beneficiary_reason || '模型正在生成受益路径…'}</p>
        </div>
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">景气判断</p>
          <p className="text-sm leading-6 text-slate-700">{cycle.prosperity_judgement || '模型正在生成景气判断…'}</p>
        </div>
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">核心逻辑</p>
          <p className="text-sm leading-6 text-slate-700">{cycle.core_logic || '模型正在补充核心逻辑…'}</p>
        </div>
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">阶段说明</p>
          <p className="text-sm leading-6 text-slate-700">{cycle.cycle_phase_reason || '模型正在补充阶段说明…'}</p>
          {cycle.killer_reason ? (
            <p className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-800">{cycle.killer_reason}</p>
          ) : null}
        </div>
      </div>

      <div className="grid gap-2 lg:grid-cols-3">
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">催化</p>
          {renderStringList(cycle.catalysts, '模型正在补充催化…')}
        </div>
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">风险</p>
          {renderStringList(cycle.risks, '模型正在补充风险…')}
        </div>
        <div className="rounded-[1.25rem] border border-slate-100 bg-white/85 px-4 py-3">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">观察点</p>
          {renderStringList(cycle.observation_points, '模型正在补充观察点…')}
        </div>
      </div>

      <div className="grid gap-2 xl:grid-cols-2">
        {renderDetectorChecklist('主线属性判定器', cycle.mainline_detector)}
        {renderDetectorChecklist('行业 Beta 判定器', cycle.industry_beta_detector)}
      </div>
    </div>
  ) : !formattedDisplayText
    ? <span className="text-slate-400">模型已启动，等待首个分片返回...</span>
    : streamKind === 'json'
      ? <pre className="market-stream-pre whitespace-pre-wrap break-words">{formattedDisplayText}</pre>
      : renderParagraphs(formattedDisplayText);

  return (
    <div className="space-y-4">
      {taskError ? (
        <InlineAlert
          title="行业周期模型研判失败"
          variant="danger"
          message={taskError}
          action={(
            <Button variant="outline" size="sm" onClick={onRegenerate}>
              重新生成
            </Button>
          )}
        />
      ) : null}

      <section className="market-mainline-surface market-mainline-status">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-sm font-medium text-slate-900">
              {isGenerating ? (taskMessage || `模型正在生成中${streamPhase ? `：${streamPhase}` : ''}`) : '当前展示最新行业周期模型输出'}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              页面展示清洗后的证据输入和模型实时输出，重点先看个股，再看行业背景。
            </p>
            {stockFocusView ? (
              <p className="mt-3 text-sm leading-7 text-slate-600">
                {stockFocusView}
              </p>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-2 text-xs text-slate-500">
              <span className={`h-2 w-2 rounded-full ${isGenerating ? 'bg-cyan-500 shadow-[0_0_0_4px_rgba(34,211,238,0.12)]' : 'bg-slate-300'}`} />
              {isGenerating ? '实时推送中' : '空闲'}
            </span>
            {displayData?.model_used ? <Badge variant="info">{displayData.model_used}</Badge> : null}
            {displayData?._cached ? <Badge variant="default">缓存</Badge> : null}
            {isGenerating ? <Badge variant="warning">SSE 推送中</Badge> : null}
            {!isGenerating ? (
              <Button variant="outline" size="sm" onClick={onRegenerate}>
                重新生成
              </Button>
            ) : null}
          </div>
        </div>
          {stockFocusSnapshot ? (
            <div className="mt-2 flex flex-wrap gap-2">
            {businessBindingStrength ? <Badge variant="warning">主营绑定 {businessBindingStrength}</Badge> : null}
            {financeState ? <Badge variant="default">财务状态 {financeState}</Badge> : null}
            {holderState ? <Badge variant="default">股东状态 {holderState}</Badge> : null}
            {tradingState ? <Badge variant="info">交易状态 {tradingState}</Badge> : null}
            {directEvidenceStrength ? <Badge variant="success">直接证据 {directEvidenceStrength}</Badge> : null}
            </div>
          ) : null}
        </section>

      <section className="market-mainline-surface space-y-4">
        <div>
          <span className="label-uppercase">Streaming Output</span>
          <h2 className="mt-1 text-2xl font-semibold text-slate-950">模型实时输出</h2>
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
            <span className={`h-2 w-2 rounded-full ${isGenerating ? 'bg-cyan-500 shadow-[0_0_0_4px_rgba(34,211,238,0.12)]' : 'bg-slate-300'}`} />
            {cycle ? '结构化报告输出' : streamKind === 'json' ? '原始 JSON 输出' : '原始报告输出'}
          </span>
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-500">
            {streamStatsText}
          </span>
          {streamPhase ? (
            <span className="rounded-full border border-cyan-200 bg-cyan-50 px-3 py-1 text-xs font-medium text-cyan-700">
              {streamPhase}
            </span>
          ) : null}
        </div>
        <div className={`market-stream-panel ${(cycle || streamKind === 'report') ? 'market-stream-report' : 'market-stream-json'}`}>
          <div className="min-h-[12rem] max-h-[34rem] overflow-auto">
            {streamContent}
          </div>
        </div>
      </section>

      <section className="market-mainline-surface">
        <button
          type="button"
          className="flex w-full items-center justify-between text-left"
          onClick={() => setShowRawInput((value) => !value)}
        >
          <div>
            <span className="label-uppercase">Input Evidence</span>
            <h3 className="mt-1 text-lg font-semibold text-slate-900">原始输入证据包</h3>
          </div>
          <ChevronDown className={cn('h-4 w-4 text-slate-400 transition-transform', showRawInput ? 'rotate-180' : '')} />
        </button>
        {showRawInput ? (
          <div className="mt-4">
            {renderEvidencePackCards(activeEvidencePack)}
          </div>
        ) : null}
      </section>

      <section className="market-mainline-surface">
        <button
          type="button"
          className="flex w-full items-center justify-between text-left"
          onClick={() => setShowRawOutput((value) => !value)}
        >
          <div>
            <span className="label-uppercase">Trace</span>
            <h3 className="mt-1 text-lg font-semibold text-slate-900">调试信息</h3>
          </div>
          <ChevronDown className={cn('h-4 w-4 text-slate-400 transition-transform', showRawOutput ? 'rotate-180' : '')} />
        </button>
        {showRawOutput ? (
          <div className="mt-4 grid gap-4">
            <div className="market-mainline-muted-block">
              <p className="mb-2 text-xs font-medium text-slate-400">System Prompt</p>
            <div className="min-w-0 market-stream-panel market-stream-json max-h-[18rem] overflow-auto rounded-[1.25rem]">
                <pre className="market-stream-pre whitespace-pre-wrap break-words">{sanitizePromptText(pickDebugInputField(activeDebugInput, 'system_prompt', 'systemPrompt') || '-')}</pre>
              </div>
            </div>
            <div className="market-mainline-muted-block">
              <p className="mb-2 text-xs font-medium text-slate-400">User Prompt</p>
              <div className="min-w-0 market-stream-panel market-stream-json max-h-[18rem] overflow-auto rounded-[1.25rem]">
                <pre className="market-stream-pre whitespace-pre-wrap break-words">{sanitizePromptText(pickDebugInputField(activeDebugInput, 'user_prompt', 'userPrompt') || '-')}</pre>
              </div>
            </div>
            <div className="market-mainline-muted-block">
              <p className="mb-2 text-xs font-medium text-slate-400">Raw Output</p>
              <div className="min-w-0 market-stream-panel market-stream-json max-h-[22rem] overflow-auto rounded-[1.25rem]">
                <pre className="market-stream-pre whitespace-pre-wrap break-words">{displayData?.raw_stream_output || displayData?.raw_response || formattedDisplayText || '-'}</pre>
              </div>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

type AnalysisMode = 'overview' | 'kline' | 'financials' | 'valuation' | 'industry-cycle' | 'shareholder' | 'news' | 'risk' | 'announcements' | 'sentiment' | 'research' | 'social';
const INDUSTRY_CYCLE_ACTIVE_TASK_STORAGE_KEY = 'industry-cycle-active-task-id';

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

  // --- Industry cycle state ---
  const [industryCycle, setIndustryCycle] = useState<IndustryCycleResponse | null>(null);
  const [industryCycleLoading, setIndustryCycleLoading] = useState(false);
  const [industryCycleTaskError, setIndustryCycleTaskError] = useState<string | null>(null);
  const [industryCycleActiveTaskId, setIndustryCycleActiveTaskId] = useState<string | null>(null);
  const [industryCycleStreamText, setIndustryCycleStreamText] = useState('');
  const [industryCycleStreamPhase, setIndustryCycleStreamPhase] = useState<string | null>(null);
  const [industryCycleTaskMessage, setIndustryCycleTaskMessage] = useState<string | null>(null);
  const [industryCycleGenerating, setIndustryCycleGenerating] = useState(false);
  const [industryCycleDebugInput, setIndustryCycleDebugInput] = useState<IndustryCycleResponse['debug_input']>(null);
  const [industryCycleDraft, setIndustryCycleDraft] = useState<IndustryCycleDraft | null>(null);

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

  useEffect(() => {
    if (mode === 'industry-cycle' && selectedSymbol) {
      void (async () => {
        setIndustryCycleLoading(true);
        try {
          const resumed = await resumeIndustryCycleTask(selectedSymbol);
          if (resumed) {
            return;
          }
          const latest = await industryCycleApi.getIndustryCycleReport(selectedSymbol);
          setIndustryCycle(latest);
          setIndustryCycleDebugInput(latest.debug_input || null);
          if (latest.report_pending || !latest.industry_cycle) {
            await startIndustryCycleGeneration(selectedSymbol, false);
          }
        } finally {
          setIndustryCycleLoading(false);
        }
      })();
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

  const fetchIndustryCycle = useCallback(async (symbol: string) => {
    setIndustryCycleLoading(true);
    try {
      const result = await industryCycleApi.getIndustryCycleReport(symbol);
      setIndustryCycle(result);
      setIndustryCycleDebugInput(result.debug_input || null);
      setIndustryCycleDraft(null);
    } catch {
      setIndustryCycle(null);
      setIndustryCycleDebugInput(null);
      setIndustryCycleDraft(null);
    } finally {
      setIndustryCycleLoading(false);
    }
  }, []);

  const rememberIndustryCycleTask = useCallback((taskId: string | null) => {
    if (typeof window === 'undefined') return;
    if (taskId) {
      window.localStorage.setItem(INDUSTRY_CYCLE_ACTIVE_TASK_STORAGE_KEY, taskId);
    } else {
      window.localStorage.removeItem(INDUSTRY_CYCLE_ACTIVE_TASK_STORAGE_KEY);
    }
  }, []);

  const applyIndustryCycleTaskState = useCallback((
    payload: IndustryCycleTaskPayload,
    message: string | null,
    taskId: string,
    status: 'pending' | 'processing' | 'completed' | 'failed',
  ) => {
    setIndustryCycleActiveTaskId(taskId);
    rememberIndustryCycleTask(taskId);
    setIndustryCycleGenerating(status === 'pending' || status === 'processing');
    setIndustryCycleTaskMessage(message);
    setIndustryCycleStreamPhase(payload.phase ?? (status === 'pending' ? 'queued' : status));
    if (typeof payload.stream_text === 'string') {
      setIndustryCycleStreamText((current) => (payload.stream_text!.length >= current.length ? payload.stream_text! : current));
    }
    if (payload.report_draft) {
      setIndustryCycleDraft((current) => mergeIndustryCycleDraft(current, payload.report_draft));
    }
    if (payload.debug_input) {
      setIndustryCycleDebugInput(payload.debug_input);
    }
    if (payload.report) {
      setIndustryCycle(payload.report);
      setIndustryCycleDebugInput(payload.report.debug_input || null);
      setIndustryCycleDraft(null);
    } else if (payload.debug_input || payload.stream_text) {
      setIndustryCycle((current) => mergeIndustryCycleTaskOverlay(current, payload));
    }
  }, [rememberIndustryCycleTask]);

  const hydrateIndustryCycleTask = useCallback(async (taskId: string) => {
    try {
      const status = await analysisApi.getStatus(taskId);
      const payload = readIndustryCycleTaskPayload(status.result);
      if (status.status === 'pending' || status.status === 'processing') {
        applyIndustryCycleTaskState(payload, status.message || '行业周期任务进行中', taskId, status.status);
        return true;
      }
      if (status.status === 'completed') {
        if (payload.report) {
          setIndustryCycle(payload.report);
          setIndustryCycleDebugInput(payload.report.debug_input || null);
          setIndustryCycleDraft(null);
        } else if (payload.debug_input || payload.stream_text) {
          setIndustryCycle((current) => mergeIndustryCycleTaskOverlay(current, payload));
          if (payload.report_draft) {
            setIndustryCycleDraft((current) => mergeIndustryCycleDraft(current, payload.report_draft));
          }
        }
        setIndustryCycleTaskError(null);
        setIndustryCycleGenerating(false);
        setIndustryCycleActiveTaskId(null);
        rememberIndustryCycleTask(null);
        setIndustryCycleStreamPhase('completed');
        setIndustryCycleTaskMessage('行业周期模型分析完成');
        if (typeof payload.stream_text === 'string') {
          setIndustryCycleStreamText(payload.stream_text);
        }
        return false;
      }
      setIndustryCycleTaskError(status.error || '行业周期模型分析失败');
      setIndustryCycleGenerating(false);
      setIndustryCycleActiveTaskId(null);
      rememberIndustryCycleTask(null);
      setIndustryCycleStreamPhase('failed');
      return false;
    } catch {
      return false;
    }
  }, [applyIndustryCycleTaskState, rememberIndustryCycleTask]);

  const resumeIndustryCycleTask = useCallback(async (symbol: string): Promise<boolean> => {
    if (typeof window !== 'undefined') {
      const rememberedTaskId = window.localStorage.getItem(INDUSTRY_CYCLE_ACTIVE_TASK_STORAGE_KEY);
      if (rememberedTaskId) {
        setIndustryCycleActiveTaskId(rememberedTaskId);
        setIndustryCycleGenerating(true);
        setIndustryCycleStreamPhase('reconnecting');
        setIndustryCycleTaskMessage('正在恢复行业周期任务');
        const resumed = await hydrateIndustryCycleTask(rememberedTaskId);
        if (resumed) {
          return true;
        }
      }
    }

    try {
      const tasksResponse = await analysisApi.getTasks({ status: 'pending,processing', limit: 50 });
      const match = tasksResponse.tasks.find(
        (task) => task.stockCode === symbol && task.reportType === 'industry_cycle_report',
      );
      if (!match) {
        return false;
      }
      return hydrateIndustryCycleTask(match.taskId);
    } catch {
      return false;
    }
  }, [hydrateIndustryCycleTask]);

  const startIndustryCycleGeneration = useCallback(async (symbol: string, force: boolean = true) => {
    setIndustryCycleTaskError(null);
    setIndustryCycleGenerating(true);
    setIndustryCycle(null);
    setIndustryCycleStreamText('');
    setIndustryCycleStreamPhase('queued');
    setIndustryCycleTaskMessage('任务已提交，等待服务端处理');
    setIndustryCycleDebugInput(null);
    setIndustryCycleDraft(null);
    try {
      const task: IndustryCycleReportTaskAccepted = await industryCycleApi.createIndustryCycleReportTask(symbol, force);
      setIndustryCycleActiveTaskId(task.task_id);
      rememberIndustryCycleTask(task.task_id);
    } catch (error) {
      const message = error instanceof Error && error.message ? error.message : '行业周期模型任务提交失败';
      setIndustryCycleTaskError(message);
      setIndustryCycleGenerating(false);
      setIndustryCycleActiveTaskId(null);
      rememberIndustryCycleTask(null);
    }
  }, [rememberIndustryCycleTask]);

  useEffect(() => {
    if (!industryCycleActiveTaskId) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void hydrateIndustryCycleTask(industryCycleActiveTaskId);
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [hydrateIndustryCycleTask, industryCycleActiveTaskId]);

  useTaskStream({
    enabled: Boolean(industryCycleActiveTaskId),
    onTaskStarted: (task) => {
      if (task.taskId !== industryCycleActiveTaskId) return;
      setIndustryCycleGenerating(true);
      setIndustryCycleStreamPhase('started');
      setIndustryCycleTaskMessage(task.message || '任务已启动');
    },
    onTaskProgress: (task) => {
      if (task.taskId !== industryCycleActiveTaskId) return;
      const payload = readIndustryCycleTaskPayload(task.result);
      applyIndustryCycleTaskState(payload, task.message || null, task.taskId, task.status);
    },
    onTaskCompleted: (task) => {
      if (task.taskId !== industryCycleActiveTaskId) return;
      const payload = readIndustryCycleTaskPayload(task.result);
      if (payload.report) {
        setIndustryCycle(payload.report);
        setIndustryCycleDraft(null);
      } else if (payload.debug_input || payload.stream_text) {
        setIndustryCycle((current) => mergeIndustryCycleTaskOverlay(current, payload));
        if (payload.report_draft) {
          setIndustryCycleDraft((current) => mergeIndustryCycleDraft(current, payload.report_draft));
        }
      }
      if (typeof payload.stream_text === 'string') {
        setIndustryCycleStreamText(payload.stream_text);
      }
      setIndustryCycleTaskError(null);
      setIndustryCycleGenerating(false);
      setIndustryCycleActiveTaskId(null);
      rememberIndustryCycleTask(null);
      setIndustryCycleStreamPhase('completed');
      setIndustryCycleTaskMessage(task.message || '行业周期模型分析完成');
      if (selectedSymbol) {
        void fetchIndustryCycle(selectedSymbol);
      }
    },
    onTaskFailed: (task) => {
      if (task.taskId !== industryCycleActiveTaskId) return;
      setIndustryCycleTaskError(task.error || task.message || '行业周期模型分析失败');
      setIndustryCycleGenerating(false);
      setIndustryCycleActiveTaskId(null);
      rememberIndustryCycleTask(null);
      setIndustryCycleStreamPhase('failed');
      setIndustryCycleTaskMessage(task.message || task.error || '行业周期模型分析失败');
    },
  });

  // --- Handlers ---
  const handleStockSelect = useCallback((code: string) => {
    setSearchValue(code);
    setSearchParams({ symbol: code });
    setIndustryCycle(null);
    setIndustryCycleActiveTaskId(null);
    rememberIndustryCycleTask(null);
    setIndustryCycleGenerating(false);
    setIndustryCycleTaskError(null);
    setIndustryCycleStreamText('');
    setIndustryCycleStreamPhase(null);
    setIndustryCycleTaskMessage(null);
    setIndustryCycleDebugInput(null);
    setIndustryCycleDraft(null);
    void fetchQuote(code);
    void fetchStockInfo(code);
    void fetchFinancials(code);
    void fetchFinancialStatements(code);
    void fetchValuation(code);
    void fetchShareholder(code);
    // K-line, news, announcements will be fetched by useEffect when mode is selected
  }, [setSearchParams, fetchQuote, fetchStockInfo, fetchFinancials, fetchFinancialStatements, fetchValuation, fetchShareholder, rememberIndustryCycleTask]);

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
                { value: 'valuation', label: '估值分析' },
                { value: 'industry-cycle', label: '行业周期' },
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
            <IndustryCyclePanel
              data={industryCycle}
              draft={industryCycleDraft}
              debugInput={industryCycleDebugInput}
              loading={industryCycleLoading}
              isGenerating={industryCycleGenerating}
              streamText={industryCycleStreamText}
              streamPhase={industryCycleStreamPhase}
              taskMessage={industryCycleTaskMessage}
              taskError={industryCycleTaskError}
              onRegenerate={() => {
                if (selectedSymbol) {
                  void startIndustryCycleGeneration(selectedSymbol, true);
                }
              }}
            />
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
