import React from 'react';
import { Activity } from 'lucide-react';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { StockContentRouter } from '../components/stockAnalysis/StockContentRouter';
import { useStockAnalysisData, type AnalysisMode } from '../hooks/useStockAnalysisData';

const MODE_OPTIONS: ReadonlyArray<{ value: AnalysisMode; label: string }> = [
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
    <div className="stock-analysis-page flex min-h-full w-full">
      <nav className="stock-analysis-sidebar shrink-0">
        {MODE_OPTIONS.map((option) => {
          const active = mode === option.value;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => s.setMode(option.value)}
              className={active ? 'is-active' : undefined}
            >
              {option.label}
            </button>
          );
        })}
      </nav>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4">
        <div className="flex shrink-0 items-center justify-end gap-3">
          <div className="min-w-0 flex-1 sm:max-w-xs">
            <StockAutocomplete
              value={s.searchValue}
              onChange={s.setSearchValue}
              onSubmit={s.handleStockSelect}
              placeholder="搜索股票代码或名称..."
              showSuggestionsOnFocus
              className="stock-analysis-input"
            />
          </div>
        </div>

        <main className="relative min-h-0 min-w-0 flex-1">
          {selectedSymbol ? (
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
          ) : (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center">
                <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-xl bg-primary/10">
                  <Activity className="h-8 w-8 text-primary" />
                </div>
                <h3 className="text-lg font-semibold text-foreground">选择一只股票开始分析</h3>
                <p className="mt-2 text-sm text-muted-foreground">在上方搜索框中输入股票代码或名称</p>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
};

export default StockAnalysisPage;
