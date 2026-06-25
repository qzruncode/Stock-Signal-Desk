import React from 'react';
import { Clock } from 'lucide-react';
import { cn } from '../../utils/cn';
import { formatNumber, formatAmount, formatPct, pctColor } from '../../utils/macro';
import { TrendIcon } from '../macro';
import type { IndicatorResponse } from '../../types/macro';
import { INDICATOR_OPTIONS } from '../../types/macro';

// ---- Sub-component ----

function IndicatorSection({
  data,
  loading,
  error,
  indicator,
}: {
  data: IndicatorResponse | null;
  loading: boolean;
  error: string | null;
  indicator: string;
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
  if (!data || !data.latest || !data.latest.period) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-4 text-center">
        <p className="text-sm text-slate-400">暂无数据</p>
      </div>
    );
  }

  const latest = data.latest;
  const hasYoy = latest.yoy != null;
  const hasMom = latest.mom != null;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-3">
          <span className="text-xs text-slate-400">最新值 ({latest.period})</span>
          <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800">
            {indicator === 'M2' || indicator === '社融' ? formatAmount(latest.value) : formatNumber(latest.value)}
          </p>
        </div>
        {hasYoy && (
          <div className="rounded-xl border border-slate-200 bg-white p-3">
            <span className="text-xs text-slate-400">同比增长</span>
            <p className={cn('mt-1 text-lg font-semibold tabular-nums', pctColor(latest.yoy))}>
              {formatPct(latest.yoy)}
            </p>
          </div>
        )}
        {hasMom && (
          <div className="rounded-xl border border-slate-200 bg-white p-3">
            <span className="text-xs text-slate-400">环比增长</span>
            <p className={cn('mt-1 text-lg font-semibold tabular-nums', pctColor(latest.mom))}>
              {formatPct(latest.mom)}
            </p>
          </div>
        )}
        {!hasYoy && !hasMom && (
          <div className="rounded-xl border border-slate-200 bg-white p-3">
            <span className="text-xs text-slate-400">趋势</span>
            <p className="mt-1 flex items-center gap-1 text-sm font-medium text-slate-700">
              <TrendIcon trend={data.trend} />
              {data.trend}
            </p>
          </div>
        )}
      </div>

      {latest.extra && Object.keys(latest.extra).length > 0 && (
        <div className="flex flex-wrap gap-3">
          {Object.entries(latest.extra).map(([key, val]) => (
            <span key={key} className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {key}: {formatNumber(val)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Panel ----

interface IndicatorPanelContentProps {
  indicators: Record<string, IndicatorResponse>;
  loading: boolean;
  error: string | null;
}

const IndicatorPanelContent: React.FC<IndicatorPanelContentProps> = ({
  indicators, loading, error,
}) => {
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">正在获取宏观经济指标...</span>
        </div>
      </div>
    );
  }

  if (error && Object.keys(indicators).length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {Object.entries(INDICATOR_OPTIONS).map(([key, label]) => {
        const data = indicators[key];
        if (!data) return null;
        return (
          <div key={key} className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-slate-900">{label}</h3>
            <IndicatorSection
              data={data}
              loading={false}
              error={data.errors.length > 0 ? data.errors.join(', ') : null}
              indicator={key}
            />
          </div>
        );
      })}

      <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
        <Clock className="h-3 w-3" />
        <span>
          数据获取时间: {new Date().toLocaleString('zh-CN')} · 实时
        </span>
        <span className="text-slate-300">|</span>
        <span>数据源: 东方财富</span>
      </div>
    </div>
  );
};

export default IndicatorPanelContent;
