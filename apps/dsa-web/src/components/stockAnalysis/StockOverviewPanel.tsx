import React from 'react';
import { RealtimeQuotePanel } from './RealtimeQuotePanel';
import FinancialPanel from '../FinancialPanel';
import { StockInfoPanel } from './StockInfoPanel';
import { MetaFooter } from './MetaFooter';
import type { RealtimeQuote } from '../../api/quotes';
import type { FinancialsResponse } from '../../api/financialsCore';
import type { StockInfo } from '../../api/stockInfo';

interface StockOverviewPanelProps {
  quoteLoading: boolean;
  quoteError: string | null;
  quote: RealtimeQuote | null;
  financialsLoading: boolean;
  financials: FinancialsResponse | null;
  stockInfoLoading: boolean;
  stockInfo: StockInfo | null;
}

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

export const StockOverviewPanel: React.FC<StockOverviewPanelProps> = ({
  quoteLoading, quoteError, quote,
  financialsLoading, financials,
  stockInfoLoading, stockInfo,
}) => (
  <div className="space-y-6">
    {quoteLoading ? (
      <Spinner label="正在获取行情数据..." />
    ) : quoteError && !quote ? (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{quoteError}</p>
      </div>
    ) : quote ? (
      <RealtimeQuotePanel quote={quote} stockInfo={stockInfo} />
    ) : (
      <EmptyBlock text="暂无行情数据" />
    )}
    {quote && (financialsLoading
      ? <Spinner size="sm" label="正在获取财务数据..." />
      : <FinancialPanel items={financials?.items ?? []} />)}
    {quote && (stockInfoLoading
      ? <Spinner size="sm" label="正在获取公司资料..." />
      : stockInfo ? <StockInfoPanel info={stockInfo} /> : null)}
    {quote && (
      <MetaFooter fetchedAt={quote._fetched_at} cached={quote._cached} source={quote.source} />
    )}
  </div>
);