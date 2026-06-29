import React from 'react';
import { Activity } from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { Select } from '../components/common';
import { StockOverviewPanel } from '../components/stockAnalysis/StockOverviewPanel';
import { StockContentRouter } from '../components/stockAnalysis/StockContentRouter';
import { useStockAnalysisData, type AnalysisMode } from '../hooks/useStockAnalysisData';

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

const StockAnalysisPage: React.FC = () => {
  const s = useStockAnalysisData();
  const { selectedSymbol, mode } = s;

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
        <main className="min-h-0 min-w-0 flex-1">
          {mode === 'overview' ? (
            <StockOverviewPanel
              quoteLoading={s.quoteLoading}
              quoteError={s.quoteError}
              quote={s.quote}
              financialsLoading={s.financialsLoading}
              financials={s.financials}
              stockInfoLoading={s.stockInfoLoading}
              stockInfo={s.stockInfo}
            />
          ) : (
            <StockContentRouter
              mode={mode}
              selectedSymbol={selectedSymbol}
              klineData={s.klineData}
              klineLoading={s.klineLoading}
              klineError={s.klineError}
              financialStatements={s.financialStatements}
              financialStatementsLoading={s.financialStatementsLoading}
              valuation={s.valuation}
              valuationLoading={s.valuationLoading}
              shareholder={s.shareholder}
              shareholderLoading={s.shareholderLoading}
              news={s.news}
              newsLoading={s.newsLoading}
              riskEvents={s.riskEvents}
              riskEventsLoading={s.riskEventsLoading}
              announcements={s.announcements}
              announcementsLoading={s.announcementsLoading}
              sentiment={s.sentiment}
              sentimentLoading={s.sentimentLoading}
              research={s.research}
              researchLoading={s.researchLoading}
              social={s.social}
              socialLoading={s.socialLoading}
            />
          )}
        </main>
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-xl bg-cyan-50">
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
