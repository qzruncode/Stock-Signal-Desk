import React, { useCallback, useEffect, useState } from 'react';
import { Clock } from 'lucide-react';
import { Select } from '../components/common';
import { macroApi } from '../api/macro';
import type {
  IndexDataResponse,
  BondYieldResponse,
  IndicatorResponse,
  SectorFlowResponse,
  MarketBreadthResponse,
} from '../types/macro';
import { BOND_COUNTRY_OPTIONS, BOND_TERM_OPTIONS, INDICATOR_OPTIONS } from '../types/macro';
import { cn } from '../utils/cn';
import { formatNumber, formatAmount, formatPct, pctColor } from '../utils/macro';
import { TrendIcon, LatestCard, HistoryTable, PageFooter } from '../components/macro';
import SectorFlowSection from '../components/SectorFlowSection';
import MarketBreadthSection from '../components/MarketBreadthSection';

// ---- Bond yield section ----
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

// ---- Macro indicator section ----
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

// ---- Index panel ----

interface IndexPanelProps {
  indexData: IndexDataResponse | null;
  loading: boolean;
  error: string | null;
}

const IndexPanel: React.FC<IndexPanelProps> = ({ indexData, loading, error }) => {
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

// ---- Page ----

type MacroDimension = 'index' | 'bond' | 'indicator' | 'sector_flow' | 'market_breadth';

const DIMENSION_OPTIONS: { value: MacroDimension; label: string }[] = [
  { value: 'index', label: '大盘指数' },
  { value: 'bond', label: '债券收益率' },
  { value: 'indicator', label: '宏观经济指标' },
  { value: 'sector_flow', label: '板块资金流向' },
  { value: 'market_breadth', label: '市场宽度' },
];

const INDEX_TABS: { code: string; label: string }[] = [
  { code: '000001', label: '上证指数' },
  { code: '399001', label: '深证成指' },
  { code: '399006', label: '创业板指' },
  { code: '000688', label: '科创50' },
];

const DEFAULT_INDEX = '000001';
const DEFAULT_DAYS = 20;
const DEFAULT_BOND_COUNTRY = 'cn';
const DEFAULT_BOND_TERM = '10y';
const DEFAULT_INDICATOR_MONTHS = 12;

const MacroDataPage: React.FC = () => {
  const [dimension, setDimension] = useState<MacroDimension>('index');

  // Index data
  const [indexCode, setIndexCode] = useState(DEFAULT_INDEX);
  const [days, setDays] = useState(DEFAULT_DAYS);
  const [indexData, setIndexData] = useState<IndexDataResponse | null>(null);
  const [indexLoading, setIndexLoading] = useState(false);
  const [indexError, setIndexError] = useState<string | null>(null);

  // Bond yield
  const [bondCountry, setBondCountry] = useState(DEFAULT_BOND_COUNTRY);
  const [bondTerm, setBondTerm] = useState(DEFAULT_BOND_TERM);
  const [bondData, setBondData] = useState<BondYieldResponse | null>(null);
  const [bondLoading, setBondLoading] = useState(false);
  const [bondError, setBondError] = useState<string | null>(null);

  // Macro indicator - flat layout, all indicators at once
  const [indicatorMonths] = useState(DEFAULT_INDICATOR_MONTHS);
  const [indicators, setIndicators] = useState<Record<string, IndicatorResponse>>({});
  const [indicatorsLoading, setIndicatorsLoading] = useState(false);
  const [indicatorsError, setIndicatorsError] = useState<string | null>(null);

  // Sector fund flow
  const [sectorFlowType, setSectorFlowType] = useState('industry');
  const [sectorFlowData, setSectorFlowData] = useState<SectorFlowResponse | null>(null);
  const [sectorFlowLoading, setSectorFlowLoading] = useState(false);
  const [sectorFlowError, setSectorFlowError] = useState<string | null>(null);

  // Market breadth
  const [marketBreadthData, setMarketBreadthData] = useState<MarketBreadthResponse | null>(null);
  const [marketBreadthLoading, setMarketBreadthLoading] = useState(false);
  const [marketBreadthError, setMarketBreadthError] = useState<string | null>(null);

  const fetchIndexData = useCallback(async () => {
    setIndexLoading(true);
    setIndexError(null);
    setIndexData(null);
    try {
      const result = await macroApi.getIndexData(indexCode, days);
      setIndexData(result);
    } catch (err: any) {
      const msg = err?.response?.data?.detail?.message || err?.message || '获取数据失败';
      setIndexError(msg);
      setIndexData(null);
    } finally {
      setIndexLoading(false);
    }
  }, [indexCode, days]);

  const fetchBondYield = useCallback(async () => {
    setBondLoading(true);
    setBondError(null);
    setBondData(null);
    try {
      const result = await macroApi.getBondYield(bondCountry, bondTerm);
      setBondData(result);
    } catch (err: any) {
      const msg = err?.response?.data?.detail?.message || err?.message || '获取数据失败';
      setBondError(msg);
      setBondData(null);
    } finally {
      setBondLoading(false);
    }
  }, [bondCountry, bondTerm]);

  const fetchAllIndicators = useCallback(async () => {
    setIndicatorsLoading(true);
    setIndicatorsError(null);
    const results: Record<string, IndicatorResponse> = {};
    await Promise.allSettled(
      Object.keys(INDICATOR_OPTIONS).map(async (key) => {
        try {
          const result = await macroApi.getIndicator(key, indicatorMonths);
          results[key] = result;
        } catch {
          results[key] = {
            indicator: key,
            indicator_name: key,
            latest: { period: '', value: null },
            history: [],
            trend: '-',
            _fetched_at: new Date().toISOString(),
            _cached: false,
            source: '-',
            errors: ['获取失败'],
          };
        }
      }),
    );
    setIndicators(results);
    setIndicatorsLoading(false);
  }, [indicatorMonths]);

  const fetchSectorFlow = useCallback(async () => {
    setSectorFlowLoading(true);
    setSectorFlowError(null);
    setSectorFlowData(null);
    try {
      const result = await macroApi.getSectorFlow(sectorFlowType, 10);
      setSectorFlowData(result);
    } catch (err: any) {
      const msg = err?.response?.data?.detail?.message || err?.message || '获取数据失败';
      setSectorFlowError(msg);
      setSectorFlowData(null);
    } finally {
      setSectorFlowLoading(false);
    }
  }, [sectorFlowType]);

  const fetchMarketBreadth = useCallback(async () => {
    setMarketBreadthLoading(true);
    setMarketBreadthError(null);
    setMarketBreadthData(null);
    try {
      const result = await macroApi.getMarketBreadth();
      setMarketBreadthData(result);
    } catch (err: any) {
      const msg = err?.response?.data?.detail?.message || err?.message || '获取数据失败';
      setMarketBreadthError(msg);
      setMarketBreadthData(null);
    } finally {
      setMarketBreadthLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchIndexData();
  }, [fetchIndexData]);

  useEffect(() => {
    if (dimension === 'bond') {
      void fetchBondYield();
    }
  }, [dimension, fetchBondYield]);

  useEffect(() => {
    if (dimension === 'indicator') {
      void fetchAllIndicators();
    }
  }, [dimension, fetchAllIndicators]);

  useEffect(() => {
    if (dimension === 'sector_flow') {
      void fetchSectorFlow();
    }
  }, [dimension, fetchSectorFlow]);

  useEffect(() => {
    if (dimension === 'market_breadth') {
      void fetchMarketBreadth();
    }
  }, [dimension, fetchMarketBreadth]);

  const renderIndexPanel = () => (
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

      <IndexPanel indexData={indexData} loading={indexLoading} error={indexError} />
    </div>
  );

  const renderBondPanel = () => (
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

  const renderIndicatorPanel = () => {
    if (indicatorsLoading) {
      return (
        <div className="flex h-40 items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
            <span className="text-sm text-slate-400">正在获取宏观经济指标...</span>
          </div>
        </div>
      );
    }

    if (indicatorsError && Object.keys(indicators).length === 0) {
      return (
        <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
          <p className="text-sm font-medium text-red-600">{indicatorsError}</p>
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

        {/* Single footer */}
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

  const renderSectorFlowPanel = () => (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="w-32 shrink-0">
          <Select
            value={sectorFlowType}
            onChange={(v) => setSectorFlowType(v)}
            options={[
              { value: 'industry', label: '行业板块' },
              { value: 'concept', label: '概念板块' },
            ]}
          />
        </div>
      </div>
      <SectorFlowSection data={sectorFlowData} loading={sectorFlowLoading} error={sectorFlowError} />
    </div>
  );

  const renderMarketBreadthPanel = () => (
    <MarketBreadthSection data={marketBreadthData} loading={marketBreadthLoading} error={marketBreadthError} />
  );

  const renderContent = () => {
    switch (dimension) {
      case 'index':
        return renderIndexPanel();
      case 'bond':
        return renderBondPanel();
      case 'indicator':
        return renderIndicatorPanel();
      case 'sector_flow':
        return renderSectorFlowPanel();
      case 'market_breadth':
        return renderMarketBreadthPanel();
      default:
        return null;
    }
  };

  return (
    <div className="flex h-[calc(100vh-2rem)] w-full flex-col gap-4 overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Macro Data</p>
          <h1 className="text-2xl font-semibold text-slate-950">宏观数据</h1>
          <p className="mt-0.5 text-sm text-slate-500">大盘指数、宏观经济指标一览</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-40 shrink-0">
            <Select
              value={dimension}
              onChange={(v) => setDimension(v as MacroDimension)}
              options={DIMENSION_OPTIONS}
            />
          </div>
        </div>
      </div>

      {/* Main content */}
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
        {renderContent()}
      </main>
    </div>
  );
};

export default MacroDataPage;
