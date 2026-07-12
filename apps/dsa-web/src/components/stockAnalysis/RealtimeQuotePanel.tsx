import { ArrowDown, ArrowUp, BarChart3, DollarSign, TrendingUp } from 'lucide-react';
import { cn } from '../../utils/cn';
import { classifyStock, MARKET_COLORS, MARKET_LABELS } from '../../utils/market';
import {
  formatAmount,
  formatMarketCap,
  formatVolume,
} from '../../utils/stockAnalysisFormat';
import type { RealtimeQuote } from '../../api/quotes';
import type { StockInfo } from '../../api/stockInfo';
import { DataItem } from './DataItem';

export function RealtimeQuotePanel({
  quote,
  stockInfo,
}: {
  quote: RealtimeQuote;
  stockInfo?: StockInfo | null;
}) {
  const changePct = quote.change_pct ?? 0;
  const isUp = changePct > 0;
  const isDown = changePct < 0;
  const changeColor = isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-slate-500';
  const { market } = classifyStock(quote.code);
  const limitRatio = market === 'cyb' || market === 'kcb' ? 0.2 : market === 'bj' ? 0.3 : quote.name.includes('ST') ? 0.05 : 0.1;
  const preClose = quote.pre_close ?? quote.price;
  const limitUpPrice = preClose ? Math.round(preClose * (1 + limitRatio) * 100) / 100 : null;
  const limitDownPrice = preClose ? Math.round(preClose * (1 - limitRatio) * 100) / 100 : null;

  return (
    <div className="stock-analysis-panel space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
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
          {changePct != null && (
            <span className={cn(
              'mt-2 inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm font-semibold tabular-nums',
              isUp ? 'bg-red-100 text-red-700' : isDown ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600',
            )}>
              {isUp ? <ArrowUp className="h-3.5 w-3.5" /> : isDown ? <ArrowDown className="h-3.5 w-3.5" /> : null}
              {changePct > 0 ? '+' : ''}{changePct.toFixed(2)}%
            </span>
          )}
          <div className="mt-1.5 text-sm tabular-nums text-slate-400">
            {quote.change_amount != null ? `${quote.change_amount > 0 ? '+' : ''}${quote.change_amount.toFixed(2)}` : '-'}
          </div>
        </div>
      </div>

      {stockInfo && (
        <section>
          <div className="border-t border-slate-100" />
          <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <DataItem label="公司全称" value={stockInfo.name || stockInfo.short_name || '-'} />
            <DataItem label="所属行业" value={stockInfo.industry || '-'} />
            <DataItem label="所属市场" value={stockInfo.market || '-'} />
            <DataItem
              label="上市日期"
              value={stockInfo.listing_date ? stockInfo.listing_date.replace(/-/g, '/') : '-'}
            />
            <DataItem
              label="成立日期"
              value={stockInfo.establish_date ? stockInfo.establish_date.replace(/-/g, '/') : '-'}
            />
            <DataItem label="注册资本" value={formatAmount(stockInfo.register_capital)} />
          </div>
        </section>
      )}

      <section>
        <div className="border-t border-slate-100" />
        <h3 className="mt-5 mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
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
      </section>

      <section>
        <div className="border-t border-slate-100" />
        <h3 className="mt-5 mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
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
      </section>

      <section>
        <div className="border-t border-slate-100" />
        <h3 className="mt-5 mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <DollarSign className="h-4 w-4 text-cyan-600" />市值与估值
        </h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <DataItem label="总市值" value={formatMarketCap(quote.total_mv)} />
          <DataItem label="流通市值" value={formatMarketCap(quote.circ_mv)} />
          <DataItem label="市净率" value={quote.pb_ratio != null ? quote.pb_ratio.toFixed(2) : '-'} />
        </div>
      </section>
    </div>
  );
}
