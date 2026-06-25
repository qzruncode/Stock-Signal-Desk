import React from 'react';
import { Select } from '../common';
import { cn } from '../../utils/cn';
import { formatNumber } from '../../utils/macro';
import { PageFooter } from '../macro';
import type { BondYieldResponse } from '../../types/macro';
import { BOND_COUNTRY_OPTIONS, BOND_TERM_OPTIONS } from '../../types/macro';

// ---- Sub-component ----

function BondYieldSection({
  data,
  loading,
  error,
}: {
  data: BondYieldResponse | null;
  loading: boolean;
  error: string | null;
}) {
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
  if (!data || data.latest_yield == null) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center">
        <p className="text-sm text-slate-400">暂无债券收益率数据</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-3">
          <span className="text-xs text-slate-400">最新收益率 ({data.term})</span>
          <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">
            {formatNumber(data.latest_yield, 2)}%
          </p>
        </div>
        {data.spread != null && (
          <div className="rounded-xl border border-slate-200 bg-white p-3">
            <span className="text-xs text-slate-400">期限利差 (10y-2y)</span>
            <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">
              {formatNumber(data.spread, 2)}%
            </p>
          </div>
        )}
      </div>

      {data.history && data.history.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white">
          <div className="px-4 py-3">
            <h3 className="text-sm font-semibold text-slate-900">近一个月走势</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-t border-slate-100 text-xs text-slate-400">
                  <th className="px-4 py-2 text-left font-medium">日期</th>
                  <th className="px-4 py-2 text-right font-medium">收益率</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((row, i) => (
                  <tr key={row.date} className={cn('border-t border-slate-50', i % 2 === 0 ? 'bg-slate-50/50' : 'bg-white')}>
                    <td className="px-4 py-2 text-slate-600">{row.date}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-slate-800">{formatNumber(row.value, 2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <PageFooter data={data} />
    </div>
  );
}

// ---- Panel with controls ----

interface BondPanelContentProps {
  bondData: BondYieldResponse | null;
  bondLoading: boolean;
  bondError: string | null;
  bondCountry: string;
  setBondCountry: (value: string) => void;
  bondTerm: string;
  setBondTerm: (value: string) => void;
}

const BondPanelContent: React.FC<BondPanelContentProps> = ({
  bondData, bondLoading, bondError,
  bondCountry, setBondCountry,
  bondTerm, setBondTerm,
}) => (
  <div className="space-y-4">
    <div className="flex items-center gap-2">
      <div className="w-28 shrink-0">
        <Select
          value={bondCountry}
          onChange={(v) => setBondCountry(v)}
          options={Object.entries(BOND_COUNTRY_OPTIONS).map(([value, label]) => ({ value, label }))}
        />
      </div>
      <div className="w-28 shrink-0">
        <Select
          value={bondTerm}
          onChange={(v) => setBondTerm(v)}
          options={Object.entries(BOND_TERM_OPTIONS).map(([value, label]) => ({ value, label }))}
        />
      </div>
    </div>
    <BondYieldSection data={bondData} loading={bondLoading} error={bondError} />
  </div>
);

export default BondPanelContent;
