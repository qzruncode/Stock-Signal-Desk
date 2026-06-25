import { ArrowDown, ArrowUp, BarChart3, DollarSign, TrendingUp } from 'lucide-react';
import { cn } from '../../utils/cn';
import { classifyStock, MARKET_COLORS, MARKET_LABELS } from '../../utils/market';
import {
  formatAmount,
  formatMarketCap,
  formatVolume,
} from '../../utils/stockAnalysisFormat';
import type { RealtimeQuote } from '../../api/quotes';
import { DataItem } from './DataItem';

export function RealtimeQuotePanel({ quote }: { quote: RealtimeQuote }) {
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
