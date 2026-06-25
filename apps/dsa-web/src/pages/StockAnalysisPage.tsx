import React from 'react';
import { Activity, Clock } from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Select } from '../components/common';
import KLineChartPanel from '../components/KLineChartPanel';
import FinancialPanel from '../components/FinancialPanel';
import FinancialStatementsPanel from '../components/FinancialStatementsPanel';
import {
  AnnouncementsPanel,
  NewsPanel,
  ResearchPanel,
  RiskEventsPanel,
  SentimentPanel,
  SocialSentimentPanel,
} from '../components/NewsAnnouncementPanel';
import { BuyCriteriaPanel } from '../components/buyCriteria/BuyCriteriaPanel';
import { RealtimeQuotePanel } from '../components/stockAnalysis/RealtimeQuotePanel';
import { StockInfoPanel } from '../components/stockAnalysis/StockInfoPanel';
import { BusinessAnalysisPanelStreaming } from '../components/stockAnalysis/BusinessAnalysisPanelStreaming';
import { ValuationRatiosPanel, PriceOverdraftPanel } from '../components/stockAnalysis/ValuationPanels';
import { ShareholderStructurePanel } from '../components/stockAnalysis/ShareholderStructurePanel';
import { useStockAnalysisData, type AnalysisMode } from '../hooks/useStockAnalysisData';
import { formatSourceChain } from '../utils/stockAnalysisFormat';

const MODE_OPTIONS: ReadonlyArray<{ value: AnalysisMode; label: string }> = [
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
];

function Spinner({ size = 'lg', label }: { size?: 'sm' | 'lg'; label: string }) {
  const isSmall = size === 'sm';
  return (
    <div className={isSmall ? 'flex h-20 items-center justify-center' : 'flex h-40 items-center justify-center'}>
      <div className={isSmall ? 'flex flex-col items-center gap-2' : 'flex flex-col items-center gap-3'}>
        <div className={isSmall
          ? 'h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan'
          : 'h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan'} />
        <span className={isSmall ? 'text-xs text-slate-400' : 'text-sm text-slate-400'}>{label}</span>
      </div>
    </div>
  );
}

function EmptyBlock({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
      <p className="text-sm text-slate-400">{text}</p>
    </div>
  );
}

function MetaFooter({
  fetchedAt,
  cached,
  source,
  sourceChain,
}: {
  fetchedAt?: string;
  cached?: boolean;
  source?: string;
  sourceChain?: string[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
      <Clock className="h-3 w-3" />
      <span>
        数据获取时间: {fetchedAt ? new Date(fetchedAt).toLocaleString('zh-CN') : '-'}
        {cached ? ' · 缓存' : ' · 实时'}
      </span>
      {(source || sourceChain) && (
        <>
          <span className="text-slate-300">|</span>
          <span>数据源: {source ?? formatSourceChain(sourceChain)}</span>
        </>
      )}
    </div>
  );
}

interface MetaCarrier {
  _fetched_at?: string;
  _cached?: boolean;
  source?: string;
  source_chain?: string[];
}

function DataSection<T extends MetaCarrier>({
  loading,
  loadingLabel,
  data,
  emptyText,
  render,
}: {
  loading: boolean;
  loadingLabel: string;
  data: T | null | undefined;
  emptyText: string;
  render: (data: T) => React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      {loading ? (
        <Spinner label={loadingLabel} />
      ) : data ? (
        <>
          {render(data)}
          <MetaFooter
            fetchedAt={data._fetched_at}
            cached={data._cached}
            source={data.source}
            sourceChain={data.source_chain}
          />
        </>
      ) : (
        <EmptyBlock text={emptyText} />
      )}
    </div>
  );
}

const StockAnalysisPage: React.FC = () => {
  const s = useStockAnalysisData();
  const { selectedSymbol, mode } = s;

  const renderOverview = () => (
    <div className="space-y-6">
      {s.quoteLoading ? (
        <Spinner label="正在获取行情数据..." />
      ) : s.quoteError && !s.quote ? (
        <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
          <p className="text-sm font-medium text-red-600">{s.quoteError}</p>
        </div>
      ) : s.quote ? (
        <RealtimeQuotePanel quote={s.quote} />
      ) : (
        <EmptyBlock text="暂无行情数据" />
      )}
      {s.quote && (s.financialsLoading
        ? <Spinner size="sm" label="正在获取财务数据..." />
        : <FinancialPanel items={s.financials?.items ?? []} />)}
      {s.quote && (s.stockInfoLoading
        ? <Spinner size="sm" label="正在获取公司资料..." />
        : s.stockInfo ? <StockInfoPanel info={s.stockInfo} /> : null)}
      {s.quote && (
        <MetaFooter fetchedAt={s.quote._fetched_at} cached={s.quote._cached} source={s.quote.source} />
      )}
    </div>
  );

  const renderContent = () => {
    if (!selectedSymbol) return null;
    switch (mode) {
      case 'overview':
        return renderOverview();
      case 'kline':
        return <KLineChartPanel data={s.klineData} loading={s.klineLoading} error={s.klineError} />;
      case 'financials':
        return (
          <DataSection
            loading={s.financialStatementsLoading}
            loadingLabel="正在获取财报数据..."
            data={s.financialStatements}
            emptyText="暂无财报数据"
            render={(d) => (
              <FinancialStatementsPanel
                balance_sheet={d.balance_sheet}
                income_statement={d.income_statement}
                cashflow={d.cashflow}
              />
            )}
          />
        );
      case 'business':
        return (
          <div className="space-y-6">
            <BusinessAnalysisPanelStreaming symbol={selectedSymbol} />
          </div>
        );
      case 'valuation':
        return (
          <DataSection
            loading={s.valuationLoading}
            loadingLabel="正在获取估值指标..."
            data={s.valuation}
            emptyText="暂无估值数据"
            render={(d) => (
              <>
                <ValuationRatiosPanel valuation={d} />
                <PriceOverdraftPanel valuation={d} />
              </>
            )}
          />
        );
      case 'industry-cycle':
        return <BuyCriteriaPanel symbol={selectedSymbol} valuation={s.valuation ?? undefined} />;
      case 'news':
        return (
          <DataSection
            loading={s.newsLoading}
            loadingLabel="正在获取相关新闻..."
            data={s.news}
            emptyText="暂无相关新闻"
            render={(d) => <NewsPanel news={d} />}
          />
        );
      case 'announcements':
        return (
          <DataSection
            loading={s.announcementsLoading}
            loadingLabel="正在获取公司公告..."
            data={s.announcements}
            emptyText="暂无公司公告"
            render={(d) => <AnnouncementsPanel announcements={d} />}
          />
        );
      case 'risk':
        return (
          <DataSection
            loading={s.riskEventsLoading}
            loadingLabel="正在扫描风险事件..."
            data={s.riskEvents}
            emptyText="暂无风险事件"
            render={(d) => <RiskEventsPanel riskEvents={d} />}
          />
        );
      case 'sentiment':
        return (
          <DataSection
            loading={s.sentimentLoading}
            loadingLabel="正在分析舆情情绪..."
            data={s.sentiment}
            emptyText="暂无舆情数据"
            render={(d) => <SentimentPanel sentiment={d} />}
          />
        );
      case 'research':
        return (
          <DataSection
            loading={s.researchLoading}
            loadingLabel="正在获取券商研报..."
            data={s.research}
            emptyText="暂无券商研报"
            render={(d) => <ResearchPanel research={d} />}
          />
        );
      case 'social':
        return (
          <DataSection
            loading={s.socialLoading}
            loadingLabel="正在分析社交情绪..."
            data={s.social}
            emptyText="暂无社交情绪数据"
            render={(d) => <SocialSentimentPanel social={d} />}
          />
        );
      case 'shareholder':
      default:
        return (
          <DataSection
            loading={s.shareholderLoading}
            loadingLabel="正在获取股东结构..."
            data={s.shareholder}
            emptyText="暂无股东结构数据"
            render={(d) => <ShareholderStructurePanel shareholder={d} />}
          />
        );
    }
  };

  return (
    <div className="stock-analysis-page flex min-h-full w-full flex-col gap-4">
      <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Stock Analysis</p>
          <h1 className="text-2xl font-semibold text-slate-950">个股分析</h1>
          <p className="mt-0.5 text-sm text-slate-500">多维度股票数据分析与诊断</p>
        </div>
        <div className="stock-analysis-toolbar">
          <div className="min-w-0 flex-1 sm:flex-none sm:w-72">
            <StockAutocomplete
              value={s.searchValue}
              onChange={s.setSearchValue}
              onSubmit={s.handleStockSelect}
              placeholder="搜索股票代码或名称..."
              showSuggestionsOnFocus
              className="stock-analysis-input"
            />
          </div>
          <div className="w-[9.5rem] shrink-0 sm:w-36">
            <Select
              value={mode}
              onChange={(v) => s.setMode(v as AnalysisMode)}
              className="stock-analysis-select"
              options={MODE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            />
          </div>
        </div>
      </div>

      {selectedSymbol ? (
        <main className="min-h-0 min-w-0 flex-1">{renderContent()}</main>
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
