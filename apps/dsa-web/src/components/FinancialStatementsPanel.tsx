import React, { useState } from 'react';
import { FileSpreadsheet } from 'lucide-react';
import type {
  BalanceSheetItem,
  IncomeStatementItem,
  CashflowItem,
} from '../api/financialStatements';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtLargeNum(value: number | null): string {
  if (value == null) return '-';
  const absVal = Math.abs(value);
  if (absVal >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (absVal >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(2);
}

function fmtNum(value: number | null): string {
  if (value == null) return '-';
  return value.toFixed(2);
}

function fmtPct(value: number | null): string {
  if (value == null) return '-';
  return `${value.toFixed(2)}%`;
}

function formatQuarter(dateStr: string | null | undefined): string {
  if (!dateStr) return '-';
  const s = String(dateStr).trim();
  // "2025-12-31" -> "2025Q4", "2026-03-31" -> "2026Q1"
  const m = s.match(/^(\d{4})-(\d{2})/);
  if (m) {
    const q = Math.ceil(parseInt(m[2], 10) / 3);
    return `${m[1]}Q${q}`;
  }
  return s;
}

// ---------------------------------------------------------------------------
// Shared table components
// ---------------------------------------------------------------------------

function DataRow<T>({
  label,
  items,
  getValue,
  fmt,
  bold,
}: {
  label: string;
  items: T[];
  getValue: (item: T) => number | null;
  fmt: (v: number | null) => string;
  bold?: boolean;
}) {
  return (
    <tr className="border-b border-slate-50">
      <td className="py-1.5 pr-3 text-xs text-slate-400 whitespace-nowrap">{label}</td>
      {items.map((item, i) => (
        <td
          key={i}
          className={cn(
            'py-1.5 text-right tabular-nums text-xs min-w-[4.5rem]',
            bold ? 'font-semibold text-slate-800' : 'text-slate-600',
          )}
        >
          {fmt(getValue(item))}
        </td>
      ))}
    </tr>
  );
}

function SectionHeader({ label, colSpan }: { label: string; colSpan: number }) {
  return (
    <tr className="border-b border-slate-50">
      <td colSpan={colSpan} className="pt-2 pb-0.5 text-[10px] font-medium text-slate-400">
        {label}
      </td>
    </tr>
  );
}

function QuarterHeader({ items }: { items: { report_date: string | null }[] }) {
  const quarters = items.map((item) => formatQuarter(item.report_date));
  return (
    <tr className="border-b-2 border-slate-100">
      <th className="pb-1.5 pr-3 text-left text-xs font-medium text-slate-400"></th>
      {quarters.map((q, i) => (
        <th key={i} className="pb-1.5 text-right text-[11px] font-semibold text-slate-500 min-w-[4.5rem]">
          {q}
        </th>
      ))}
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Sub-panel: Balance Sheet
// ---------------------------------------------------------------------------

function BalanceSheetTable({ items }: { items: BalanceSheetItem[] }) {
  const colSpan = items.length + 1;

  return (
    <div className="overflow-x-auto -mx-5 px-5">
      <table className="w-max">
        <thead>
          <QuarterHeader items={items} />
        </thead>
        <tbody>
          <SectionHeader label="资产" colSpan={colSpan} />
          <DataRow label="总资产" items={items} getValue={(i) => i.total_assets} fmt={fmtLargeNum} bold />
          <DataRow label="货币资金" items={items} getValue={(i) => i.monetary_funds} fmt={fmtLargeNum} />
          <DataRow label="应收账款" items={items} getValue={(i) => i.accounts_receivable} fmt={fmtLargeNum} />
          <DataRow label="存货" items={items} getValue={(i) => i.inventory} fmt={fmtLargeNum} />
          <DataRow label="固定资产" items={items} getValue={(i) => i.fixed_asset} fmt={fmtLargeNum} />

          <SectionHeader label="负债" colSpan={colSpan} />
          <DataRow label="总负债" items={items} getValue={(i) => i.total_liabilities} fmt={fmtLargeNum} bold />
          <DataRow label="短期借款" items={items} getValue={(i) => i.short_loan} fmt={fmtLargeNum} />
          <DataRow label="应付账款" items={items} getValue={(i) => i.accounts_payable} fmt={fmtLargeNum} />
          <DataRow label="一年内到期非流动负债" items={items} getValue={(i) => i.noncurrent_liab_1year} fmt={fmtLargeNum} />
          <DataRow label="长期借款" items={items} getValue={(i) => i.long_loan} fmt={fmtLargeNum} />
          <DataRow label="租赁负债" items={items} getValue={(i) => i.lease_liab} fmt={fmtLargeNum} />

          <SectionHeader label="股东权益" colSpan={colSpan} />
          <DataRow label="股东权益" items={items} getValue={(i) => i.total_equity} fmt={fmtLargeNum} bold />

          <SectionHeader label="财务比率" colSpan={colSpan} />
          <DataRow label="资产负债率" items={items} getValue={(i) => i.debt_ratio} fmt={fmtPct} />
          <DataRow label="权益乘数" items={items} getValue={(i) => i.equity_multiplier} fmt={fmtNum} />
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-panel: Income Statement
// ---------------------------------------------------------------------------

function IncomeStatementTable({ items }: { items: IncomeStatementItem[] }) {
  const colSpan = items.length + 1;

  return (
    <div className="overflow-x-auto -mx-5 px-5">
      <table className="w-max">
        <thead>
          <QuarterHeader items={items} />
        </thead>
        <tbody>
          <SectionHeader label="收入与成本" colSpan={colSpan} />
          <DataRow label="营业总收入" items={items} getValue={(i) => i.revenue} fmt={fmtLargeNum} bold />
          <DataRow label="营业成本" items={items} getValue={(i) => i.operate_cost} fmt={fmtLargeNum} />
          <DataRow label="销售费用" items={items} getValue={(i) => i.sale_expense} fmt={fmtLargeNum} />
          <DataRow label="管理费用" items={items} getValue={(i) => i.manage_expense} fmt={fmtLargeNum} />
          <DataRow label="研发费用" items={items} getValue={(i) => i.research_expense} fmt={fmtLargeNum} />

          <SectionHeader label="利润" colSpan={colSpan} />
          <DataRow label="营业利润" items={items} getValue={(i) => i.operate_profit} fmt={fmtLargeNum} bold />
          <DataRow label="净利润" items={items} getValue={(i) => i.net_profit} fmt={fmtLargeNum} bold />
          <DataRow label="扣非净利润" items={items} getValue={(i) => i.deducted_net_profit} fmt={fmtLargeNum} />

          <SectionHeader label="盈利能力" colSpan={colSpan} />
          <DataRow label="毛利率" items={items} getValue={(i) => i.gross_margin} fmt={fmtPct} />
          <DataRow label="净利率" items={items} getValue={(i) => i.net_margin} fmt={fmtPct} />
          <DataRow label="基本每股收益" items={items} getValue={(i) => i.basic_eps} fmt={fmtNum} />
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-panel: Cash Flow Statement
// ---------------------------------------------------------------------------

function CashflowTable({ items }: { items: CashflowItem[] }) {
  const colSpan = items.length + 1;

  return (
    <div className="overflow-x-auto -mx-5 px-5">
      <table className="w-max">
        <thead>
          <QuarterHeader items={items} />
        </thead>
        <tbody>
          <SectionHeader label="现金流" colSpan={colSpan} />
          <DataRow label="经营活动" items={items} getValue={(i) => i.operating_cf} fmt={fmtLargeNum} bold />
          <DataRow label="投资活动" items={items} getValue={(i) => i.investing_cf} fmt={fmtLargeNum} />
          <DataRow label="筹资活动" items={items} getValue={(i) => i.financing_cf} fmt={fmtLargeNum} />
          <DataRow label="资本支出" items={items} getValue={(i) => i.capex} fmt={fmtLargeNum} />

          <SectionHeader label="现金流质量" colSpan={colSpan} />
          <DataRow label="自由现金流" items={items} getValue={(i) => i.free_cashflow} fmt={fmtLargeNum} bold />
          <DataRow label="经营CF/净利润" items={items} getValue={(i) => i.cf_quality} fmt={fmtNum} />
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

type StatementTab = 'balance' | 'income' | 'cashflow';

interface FinancialStatementsPanelProps {
  balance_sheet: BalanceSheetItem[];
  income_statement: IncomeStatementItem[];
  cashflow: CashflowItem[];
}

const FinancialStatementsPanel: React.FC<FinancialStatementsPanelProps> = ({
  balance_sheet,
  income_statement,
  cashflow,
}) => {
  const [tab, setTab] = useState<StatementTab>('balance');

  const hasData = balance_sheet.length > 0 || income_statement.length > 0 || cashflow.length > 0;
  if (!hasData) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center">
        <p className="text-sm text-slate-400">暂无财务报表数据</p>
      </div>
    );
  }

  const tabs: { key: StatementTab; label: string; count: number }[] = [
    { key: 'balance', label: '资产负债表', count: balance_sheet.length },
    { key: 'income', label: '利润表', count: income_statement.length },
    { key: 'cashflow', label: '现金流量表', count: cashflow.length },
  ];

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm w-full overflow-hidden">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <FileSpreadsheet className="h-4 w-4 text-cyan-600" />
        财报分析
        <span className="ml-auto text-xs font-normal text-slate-400">
          最近 {balance_sheet.length || income_statement.length || cashflow.length} 个季度
        </span>
      </h3>

      {/* Tab bar */}
      <div className="mb-4 flex gap-1 border-b border-slate-100">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'px-3 py-1.5 text-xs font-medium rounded-t-lg transition-colors',
              'border border-b-0 -mb-px',
              tab === t.key
                ? 'bg-white text-cyan-700 border-slate-200'
                : 'text-slate-400 border-transparent hover:text-slate-600 hover:bg-slate-50',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === 'balance' && <BalanceSheetTable items={balance_sheet} />}
      {tab === 'income' && <IncomeStatementTable items={income_statement} />}
      {tab === 'cashflow' && <CashflowTable items={cashflow} />}
    </div>
  );
};

export default FinancialStatementsPanel;
