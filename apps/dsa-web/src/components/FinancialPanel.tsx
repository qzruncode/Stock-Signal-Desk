import React from 'react';
import { Calculator } from 'lucide-react';
import type { FinancialItem } from '../api/financials';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtPct(value: number | null): string {
  if (value == null) return '-';
  return `${value.toFixed(2)}%`;
}

function fmtNum(value: number | null): string {
  if (value == null) return '-';
  return value.toFixed(2);
}

function fmtLargeNum(value: number | null): string {
  if (value == null) return '-';
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

function formatPeriod(dateStr: string | null | undefined): string {
  if (!dateStr) return '-';
  const s = String(dateStr).trim();
  // "2025-12-31" → "2025Q4", "2026-03-31" → "2026Q1"
  const m = s.match(/^(\d{4})-(\d{2})/);
  if (m) {
    const q = Math.ceil(parseInt(m[2], 10) / 3);
    return `${m[1]}Q${q}`;
  }
  // "20251231" → "2025Q4"
  const m2 = s.match(/^(\d{4})(\d{2})/);
  if (m2) {
    const q = Math.ceil(parseInt(m2[2], 10) / 3);
    return `${m2[1]}Q${q}`;
  }
  return s;
}

function growthColor(val: number | null): string {
  if (val == null) return 'text-slate-600';
  return val > 0 ? 'text-red-600' : val < 0 ? 'text-green-600' : 'text-slate-600';
}

// ---------------------------------------------------------------------------
// Table row
// ---------------------------------------------------------------------------

function DataRow({
  label,
  items,
  getValue,
  fmt,
  bold,
}: {
  label: string;
  items: FinancialItem[];
  getValue: (item: FinancialItem) => number | null;
  fmt: (v: number | null) => string;
  bold?: boolean;
}) {
  return (
    <tr className="border-b border-slate-50">
      <td className="py-2 pr-4 text-xs text-slate-400 whitespace-nowrap">{label}</td>
      {items.map((item, i) => (
        <td
          key={i}
          className={cn(
            'py-2 text-right tabular-nums text-xs min-w-20',
            bold ? 'font-semibold text-slate-800' : 'text-slate-600',
          )}
        >
          {fmt(getValue(item))}
        </td>
      ))}
    </tr>
  );
}

function GrowthRow({
  label,
  items,
  getValue,
}: {
  label: string;
  items: FinancialItem[];
  getValue: (item: FinancialItem) => number | null;
}) {
  return (
    <tr className="border-b border-slate-50">
      <td className="py-2 pr-4 text-xs text-slate-400 whitespace-nowrap">{label}</td>
      {items.map((item, i) => (
        <td
          key={i}
          className={cn(
            'py-2 text-right tabular-nums text-xs min-w-20',
            growthColor(getValue(item)),
          )}
        >
          {fmtPct(getValue(item))}
        </td>
      ))}
    </tr>
  );
}

function SectionHeader({ label, colSpan }: { label: string; colSpan: number }) {
  return (
    <tr className="border-b border-slate-50">
      <td colSpan={colSpan} className="pt-3 pb-1 text-xs font-medium text-slate-400">
        {label}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface FinancialPanelProps {
  items: FinancialItem[];
}

const FinancialPanel: React.FC<FinancialPanelProps> = ({ items }) => {
  if (!items || items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center">
        <p className="text-sm text-slate-400">暂无财务数据</p>
      </div>
    );
  }

  const years = items.map((item) => formatPeriod(item.report_date));
  const colSpan = years.length + 1;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm w-full overflow-hidden">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Calculator className="h-4 w-4 text-cyan-600" />
        核心财务指标
      </h3>

      <div className="overflow-x-auto -mx-5 px-5">
        <table className="w-max">
          <thead>
            <tr className="border-b-2 border-slate-100">
              <th className="pb-2 pr-4 text-left text-xs font-medium text-slate-400"></th>
              {years.map((year, i) => (
                <th key={i} className="pb-2 text-right text-xs font-semibold text-slate-500 min-w-20">
                  {year}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            <SectionHeader label="营收与利润" colSpan={colSpan} />
            <DataRow label="营业总收入" items={items} getValue={(item) => item.revenue} fmt={fmtLargeNum} bold />
            <DataRow label="净利润" items={items} getValue={(item) => item.net_profit} fmt={fmtLargeNum} bold />
            <DataRow label="扣非净利润" items={items} getValue={(item) => item.deducted_profit} fmt={fmtLargeNum} />

            <SectionHeader label="盈利能力" colSpan={colSpan} />
            <DataRow label="ROE" items={items} getValue={(item) => item.roe} fmt={fmtPct} bold />
            <DataRow label="毛利率" items={items} getValue={(item) => item.gross_margin} fmt={fmtPct} />
            <DataRow label="净利率" items={items} getValue={(item) => item.net_margin} fmt={fmtPct} />

            <SectionHeader label="成长性" colSpan={colSpan} />
            <GrowthRow label="营收增长率" items={items} getValue={(item) => item.revenue_yoy} />
            <GrowthRow label="净利增长率" items={items} getValue={(item) => item.net_profit_yoy} />
            <GrowthRow label="扣非增长率" items={items} getValue={(item) => item.deducted_profit_yoy} />

            <SectionHeader label="每股指标" colSpan={colSpan} />
            <DataRow label="每股收益(EPS)" items={items} getValue={(item) => item.eps} fmt={fmtNum} bold />
            <DataRow label="每股净资产(BPS)" items={items} getValue={(item) => item.bps} fmt={fmtNum} />

            <SectionHeader label="偿债能力" colSpan={colSpan} />
            <DataRow label="资产负债率" items={items} getValue={(item) => item.debt_ratio} fmt={fmtPct} />
            <DataRow label="流动比率" items={items} getValue={(item) => item.current_ratio} fmt={fmtNum} />
            <DataRow label="速动比率" items={items} getValue={(item) => item.quick_ratio} fmt={fmtNum} />
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default FinancialPanel;
