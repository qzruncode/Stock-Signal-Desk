import React, { useState } from 'react';
import { cn } from '../utils/cn';
import type { SectorFlowResponse, SectorFlowRecord } from '../types/macro';
import { formatPct, formatAmount, pctColor } from '../utils/macro';
import { ArrowIcon, PageFooter } from './macro';

interface SectorFlowSectionProps {
  data: SectorFlowResponse | null;
  loading: boolean;
  error: string | null;
}

type FlowTab = 'inflow' | 'outflow';

function hasDetailFlow(rec: SectorFlowRecord): boolean {
  return rec.main_net_inflow != null;
}

function FlowTable({
  records,
  label,
}: {
  records: SectorFlowRecord[];
  label: string;
}) {
  if (!records || records.length === 0) return null;

  const sample = records[0];
  const hasDetailedFlow = hasDetailFlow(sample);
  const hasLeadingStock = sample.leading_stock != null && sample.leading_stock !== '';

  return (
    <div className="rounded-2xl border border-slate-200 bg-white">
      <div className="px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-900">{label}</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-t border-slate-100 text-xs text-slate-400">
              <th className="px-4 py-2 text-left font-medium">板块</th>
              <th className="px-4 py-2 text-right font-medium">涨跌幅</th>
              {hasDetailedFlow && (
                <th className="px-4 py-2 text-right font-medium">主力净流入</th>
              )}
              {sample.super_large_net_inflow != null && (
                <th className="px-4 py-2 text-right font-medium">超大单净流入</th>
              )}
              {sample.large_net_inflow != null && (
                <th className="px-4 py-2 text-right font-medium">大单净流入</th>
              )}
              {sample.total_amount != null && (
                <th className="px-4 py-2 text-right font-medium">成交额</th>
              )}
              {hasLeadingStock && (
                <th className="px-4 py-2 text-right font-medium">领涨股</th>
              )}
            </tr>
          </thead>
          <tbody>
            {records.map((rec, i) => (
              <tr key={rec.name} className={cn('border-t border-slate-50', i % 2 === 0 ? 'bg-slate-50/50' : 'bg-white')}>
                <td className="px-4 py-2 font-medium text-slate-800">{rec.name}</td>
                <td className={cn('px-4 py-2 text-right tabular-nums', pctColor(rec.pct_chg))}>
                  <ArrowIcon value={rec.pct_chg} />
                  <span className="ml-1">{formatPct(rec.pct_chg)}</span>
                </td>
                {hasDetailedFlow && (
                  <td className={cn('px-4 py-2 text-right tabular-nums', pctColor(rec.main_net_inflow))}>
                    {formatAmount(rec.main_net_inflow)}
                  </td>
                )}
                {sample.super_large_net_inflow != null && (
                  <td className={cn('px-4 py-2 text-right tabular-nums', pctColor(rec.super_large_net_inflow))}>
                    {formatAmount(rec.super_large_net_inflow)}
                  </td>
                )}
                {sample.large_net_inflow != null && (
                  <td className={cn('px-4 py-2 text-right tabular-nums', pctColor(rec.large_net_inflow))}>
                    {formatAmount(rec.large_net_inflow)}
                  </td>
                )}
                {sample.total_amount != null && (
                  <td className={cn('px-4 py-2 text-right tabular-nums', pctColor(rec.total_amount))}>
                    {formatAmount(rec.total_amount)}
                  </td>
                )}
                {hasLeadingStock && (
                  <td className="px-4 py-2 text-right text-slate-600">{rec.leading_stock}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const SectorFlowSection: React.FC<SectorFlowSectionProps> = ({ data, loading, error }) => {
  const [tab, setTab] = useState<FlowTab>('inflow');

  if (loading && !data) {
    return (
      <div className="flex h-20 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-4 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center">
        <p className="text-sm text-slate-400">暂无板块资金流向数据</p>
      </div>
    );
  }

  const inflow = data.inflow_top ?? [];
  const outflow = data.outflow_top ?? [];
  const hasFlow = inflow.length > 0 || outflow.length > 0;

  // 降级: 没有流入/流出分离数据时用 records
  const displayInflow = hasFlow ? inflow : (data.records ?? []);
  const displayOutflow: SectorFlowRecord[] = hasFlow ? outflow : [];

  return (
    <div className="space-y-3">
      {/* Tab switch */}
      <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1">
        <button
          type="button"
          onClick={() => setTab('inflow')}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm font-medium transition',
            tab === 'inflow' ? 'bg-red-50 text-red-700 shadow-sm' : 'text-slate-500 hover:text-slate-700',
          )}
        >
          资金流入 Top {data.top_n}
        </button>
        <button
          type="button"
          onClick={() => setTab('outflow')}
          className={cn(
            'rounded-md px-3 py-1.5 text-sm font-medium transition',
            tab === 'outflow' ? 'bg-green-50 text-green-700 shadow-sm' : 'text-slate-500 hover:text-slate-700',
          )}
        >
          资金流出 Top {data.top_n}
        </button>
      </div>

      {/* Table */}
      {tab === 'inflow' && (
        <FlowTable records={displayInflow} label={`资金净流入 Top ${data.top_n} (${data.type === 'industry' ? '行业' : '概念'})`} />
      )}
      {tab === 'outflow' && displayOutflow.length > 0 && (
        <FlowTable records={displayOutflow} label={`资金净流出 Top ${data.top_n} (${data.type === 'industry' ? '行业' : '概念'})`} />
      )}
      {tab === 'outflow' && displayOutflow.length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center">
          <p className="text-sm text-slate-400">暂无资金流出数据</p>
        </div>
      )}

      <PageFooter data={data} />
    </div>
  );
};

export default SectorFlowSection;
