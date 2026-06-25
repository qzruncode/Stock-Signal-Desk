import React from 'react';
import { cn } from '../../utils/cn';
import { LatestCard, HistoryTable, PageFooter } from '../macro';
import type { IndexDataResponse } from '../../types/macro';

const INDEX_TABS: { code: string; label: string }[] = [
  { code: '000001', label: '上证指数' },
  { code: '399001', label: '深证成指' },
  { code: '399006', label: '创业板指' },
  { code: '000688', label: '科创50' },
];

// ---- Sub-component ----

const IndexDataPanel: React.FC<{
  indexData: IndexDataResponse | null;
  loading: boolean;
  error: string | null;
}> = ({ indexData, loading, error }) => {
  if (loading && !indexData) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">正在获取指数数据...</span>
        </div>
      </div>
    );
  }
  if (error && !indexData) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }
  if (!indexData || !indexData.latest || !indexData.latest.close) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无数据</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <LatestCard latest={indexData.latest} />
      <HistoryTable history={indexData.history} />
      <PageFooter data={indexData} />
    </div>
  );
};

// ---- Panel with controls ----

interface IndexPanelContentProps {
  indexData: IndexDataResponse | null;
  loading: boolean;
  error: string | null;
  indexCode: string;
  setIndexCode: (code: string) => void;
  days: number;
  setDays: (days: number) => void;
}

const IndexPanelContent: React.FC<IndexPanelContentProps> = ({
  indexData, loading, error,
  indexCode, setIndexCode,
  days, setDays,
}) => (
  <div className="space-y-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1">
        {INDEX_TABS.map((tab) => (
          <button
            key={tab.code}
            type="button"
            onClick={() => setIndexCode(tab.code)}
            className={cn(
              'rounded-md px-3 py-1.5 text-sm font-medium transition',
              indexCode === tab.code
                ? 'bg-cyan-50 text-cyan-700 shadow-sm'
                : 'text-slate-500 hover:text-slate-700',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <label htmlFor="days" className="text-xs text-slate-500">天数</label>
        <input
          id="days"
          type="number"
          min={1}
          max={500}
          value={days}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            if (!isNaN(v) && v >= 1 && v <= 500) setDays(v);
          }}
          className="h-9 w-16 rounded-lg border border-slate-200 bg-white px-2 text-sm tabular-nums text-slate-700 focus:border-cyan-400 focus:outline-none"
        />
      </div>
    </div>

    <IndexDataPanel indexData={indexData} loading={loading} error={error} />
  </div>
);

export default IndexPanelContent;
