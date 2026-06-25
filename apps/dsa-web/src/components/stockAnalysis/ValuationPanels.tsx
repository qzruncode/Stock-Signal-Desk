import { AlertTriangle, Percent } from 'lucide-react';
import { cn } from '../../utils/cn';
import {
  formatPctValue,
  formatRatio,
} from '../../utils/stockAnalysisFormat';
import type { ValuationRatiosResponse } from '../../api/valuation';
import { DataItem } from './DataItem';

export function ValuationRatiosPanel({ valuation }: { valuation: ValuationRatiosResponse }) {
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

export function PriceOverdraftPanel({ valuation }: { valuation: ValuationRatiosResponse }) {
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
