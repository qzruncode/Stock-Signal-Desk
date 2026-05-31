import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Clock,
  Globe,
  Layers,
  Minus,
} from 'lucide-react';
import { Select } from '../components/common';
import { marketApi, type MarketStatus } from '../api/market';
import { sectorsApi, type SectorListResponse, type SectorType } from '../api/sectors';
import MarketOverviewPanel from '../components/MarketOverviewPanel';
import { cn } from '../utils/cn';

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatPct(value: number | null | undefined): string {
  if (value == null) return '-';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function formatFlow(value: number | null | undefined): string {
  if (value == null) return '-';
  const abs = Math.abs(value);
  if (abs >= 1e4) return `${(value / 1e4).toFixed(2)}亿`;
  if (abs >= 1e2) return `${(value / 1e2).toFixed(0)}亿`;
  return `${value.toFixed(2)}亿`;
}

// ---------------------------------------------------------------------------
// Industry Table (with lead_stock, up/down counts)
// ---------------------------------------------------------------------------

type IndustrySortField = 'change_pct' | 'up_count' | 'down_count' | 'net_flow' | 'name';

function IndustryTable({
  data,
  loading,
  error,
}: {
  data: SectorListResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const [sortField, setSortField] = useState<IndustrySortField>('change_pct');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">正在获取行业数据...</span>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (!data || !data.items || data.items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无行业数据</p>
      </div>
    );
  }

  const sorted = [...data.items].sort((a, b) => {
    if (sortField === 'name') {
      return sortDir === 'asc'
        ? (a.name || '').localeCompare(b.name || '', 'zh')
        : (b.name || '').localeCompare(a.name || '', 'zh');
    }
    const va = (a[sortField] as number) ?? 0;
    const vb = (b[sortField] as number) ?? 0;
    return sortDir === 'asc' ? va - vb : vb - va;
  });

  const handleSort = (field: IndustrySortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const SortIcon = ({ field }: { field: IndustrySortField }) => {
    if (sortField !== field) return <Minus className="h-3 w-3 text-slate-300" />;
    return sortDir === 'asc'
      ? <ArrowUp className="h-3 w-3 text-cyan-600" />
      : <ArrowDown className="h-3 w-3 text-cyan-600" />;
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-18rem)]">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10">
            <tr className="border-b border-slate-200 bg-slate-50/70 backdrop-blur-sm">
              <th className="cursor-pointer px-4 py-3 text-left font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('name')}>
                <span className="inline-flex items-center gap-1">行业 <SortIcon field="name" /></span>
              </th>
              <th className="cursor-pointer px-3 py-3 text-right font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('change_pct')}>
                <span className="inline-flex items-center justify-end gap-1">涨跌幅 <SortIcon field="change_pct" /></span>
              </th>
              <th className="px-3 py-3 text-left font-medium text-slate-600">领涨股</th>
              <th className="cursor-pointer px-3 py-3 text-right font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('up_count')}>
                <span className="inline-flex items-center justify-end gap-1">上涨 <SortIcon field="up_count" /></span>
              </th>
              <th className="cursor-pointer px-3 py-3 text-right font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('down_count')}>
                <span className="inline-flex items-center justify-end gap-1">下跌 <SortIcon field="down_count" /></span>
              </th>
              <th className="cursor-pointer px-3 py-3 text-right font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('net_flow')}>
                <span className="inline-flex items-center justify-end gap-1">净流入 <SortIcon field="net_flow" /></span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item, idx) => {
              const isUp = (item.change_pct ?? 0) > 0;
              const isDown = (item.change_pct ?? 0) < 0;
              const changeColor = isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-slate-400';
              const flowUp = (item.net_flow ?? 0) > 0;
              const flowDown = (item.net_flow ?? 0) < 0;
              const flowColor = flowUp ? 'text-red-500' : flowDown ? 'text-green-500' : 'text-slate-400';

              return (
                <tr key={item.code || idx} className={cn('border-b border-slate-100 transition-colors hover:bg-slate-50', idx % 2 === 0 && 'bg-white/50')}>
                  <td className="px-4 py-2.5 font-medium text-slate-800">{item.name}</td>
                  <td className={cn('px-3 py-2.5 text-right font-semibold tabular-nums', changeColor)}>{formatPct(item.change_pct)}</td>
                  <td className="px-3 py-2.5 text-slate-600">
                    {item.lead_stock ? (
                      <span>
                        {item.lead_stock}
                        {item.lead_stock_change_pct != null && (
                          <span className={cn('ml-1 text-xs', (item.lead_stock_change_pct ?? 0) > 0 ? 'text-red-500' : 'text-green-500')}>
                            {formatPct(item.lead_stock_change_pct)}
                          </span>
                        )}
                      </span>
                    ) : '-'}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-red-500">{item.up_count != null ? item.up_count : '-'}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-green-500">{item.down_count != null ? item.down_count : '-'}</td>
                  <td className={cn('px-3 py-2.5 text-right tabular-nums', flowColor)}>{formatFlow(item.net_flow)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-1.5 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-400">
        <Clock className="h-3 w-3" />
        <span>
          数据获取时间: {data._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
          {data._cached ? ' · 缓存' : ' · 实时'}
          {' · '}共 {data.items.length} 个行业
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Concept Table (simpler, just name + 涨跌幅 + net_flow)
// ---------------------------------------------------------------------------

type ConceptSortField = 'change_pct' | 'net_flow' | 'name';

function ConceptTable({
  data,
  loading,
  error,
}: {
  data: SectorListResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const [sortField, setSortField] = useState<ConceptSortField>('change_pct');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          <span className="text-sm text-slate-400">正在获取概念数据...</span>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (!data || !data.items || data.items.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
        <p className="text-sm text-slate-400">暂无概念数据</p>
      </div>
    );
  }

  const sorted = [...data.items].sort((a, b) => {
    if (sortField === 'name') {
      return sortDir === 'asc'
        ? (a.name || '').localeCompare(b.name || '', 'zh')
        : (b.name || '').localeCompare(a.name || '', 'zh');
    }
    const va = (a[sortField] as number) ?? 0;
    const vb = (b[sortField] as number) ?? 0;
    return sortDir === 'asc' ? va - vb : vb - va;
  });

  const handleSort = (field: ConceptSortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const SortIcon = ({ field }: { field: ConceptSortField }) => {
    if (sortField !== field) return <Minus className="h-3 w-3 text-slate-300" />;
    return sortDir === 'asc'
      ? <ArrowUp className="h-3 w-3 text-cyan-600" />
      : <ArrowDown className="h-3 w-3 text-cyan-600" />;
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/88 shadow-sm">
      <div className="overflow-x-auto overflow-y-auto max-h-[calc(100vh-18rem)]">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10">
            <tr className="border-b border-slate-200 bg-slate-50/70 backdrop-blur-sm">
              <th className="cursor-pointer px-4 py-3 text-left font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('name')}>
                <span className="inline-flex items-center gap-1">概念板块 <SortIcon field="name" /></span>
              </th>
              <th className="cursor-pointer px-3 py-3 text-right font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('change_pct')}>
                <span className="inline-flex items-center justify-end gap-1">涨跌幅 <SortIcon field="change_pct" /></span>
              </th>
              <th className="cursor-pointer px-3 py-3 text-right font-medium text-slate-600 hover:text-slate-900" onClick={() => handleSort('net_flow')}>
                <span className="inline-flex items-center justify-end gap-1">主力净流入 <SortIcon field="net_flow" /></span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item, idx) => {
              const isUp = (item.change_pct ?? 0) > 0;
              const isDown = (item.change_pct ?? 0) < 0;
              const changeColor = isUp ? 'text-red-600' : isDown ? 'text-green-600' : 'text-slate-400';
              const flowUp = (item.net_flow ?? 0) > 0;
              const flowDown = (item.net_flow ?? 0) < 0;
              const flowColor = flowUp ? 'text-red-500' : flowDown ? 'text-green-500' : 'text-slate-400';

              return (
                <tr key={item.code || item.name || idx} className={cn('border-b border-slate-100 transition-colors hover:bg-slate-50', idx % 2 === 0 && 'bg-white/50')}>
                  <td className="px-4 py-2.5">
                    <span className="font-medium text-slate-800">{item.name}</span>
                    {item.code && <span className="ml-2 text-xs text-slate-400">{item.code}</span>}
                  </td>
                  <td className={cn('px-3 py-2.5 text-right font-semibold tabular-nums', changeColor)}>{formatPct(item.change_pct)}</td>
                  <td className={cn('px-3 py-2.5 text-right tabular-nums', flowColor)}>{formatFlow(item.net_flow)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-1.5 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-400">
        <Clock className="h-3 w-3" />
        <span>
          数据获取时间: {data._fetched_at ? new Date(data._fetched_at).toLocaleString('zh-CN') : '-'}
          {data._cached ? ' · 缓存' : ' · 实时'}
          {' · '}共 {data.items.length} 个概念板块
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

type AnalysisMode = 'market' | 'industry' | 'concept';

const MarketAnalysisPage: React.FC = () => {
  const [marketData, setMarketData] = useState<MarketStatus | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState<string | null>(null);

  const [sectorData, setSectorData] = useState<SectorListResponse | null>(null);
  const [sectorLoading, setSectorLoading] = useState(false);
  const [sectorError, setSectorError] = useState<string | null>(null);

  const [mode, setMode] = useState<AnalysisMode>('market');

  useEffect(() => {
    if (mode === 'market') {
      void fetchMarketStatus();
    }
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (mode === 'industry' || mode === 'concept') {
      void fetchSectors(mode);
    }
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchMarketStatus = useCallback(async () => {
    setMarketLoading(true);
    setMarketError(null);
    try {
      const result = await marketApi.getStatus();
      setMarketData(result);
    } catch {
      setMarketData(null);
      setMarketError('获取市场数据失败，请稍后重试');
    } finally {
      setMarketLoading(false);
    }
  }, []);

  const fetchSectors = useCallback(async (sectorType: SectorType) => {
    setSectorLoading(true);
    setSectorError(null);
    try {
      const result = await sectorsApi.getSectors(sectorType);
      setSectorData(result);
    } catch {
      setSectorData(null);
      setSectorError('获取板块数据失败，请稍后重试');
    } finally {
      setSectorLoading(false);
    }
  }, []);

  return (
    <div className="flex h-[calc(100vh-2rem)] w-full flex-col gap-4 overflow-hidden">
      <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Market Analysis</p>
          <h1 className="text-2xl font-semibold text-slate-950">市场分析</h1>
          <p className="mt-0.5 text-sm text-slate-500">市场概况与行业/概念板块分析</p>
        </div>
        <div className="w-36 shrink-0">
          <Select
            value={mode}
            onChange={(v) => setMode(v as AnalysisMode)}
            options={[
              { value: 'market', label: '市场概况' },
              { value: 'industry', label: '行业板块' },
              { value: 'concept', label: '概念板块' },
            ]}
          />
        </div>
      </div>

      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {mode === 'market' ? (
          <MarketOverviewPanel data={marketData} loading={marketLoading} error={marketError} />
        ) : mode === 'industry' ? (
          <div className="space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
              <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-700">
                <Layers className="h-4 w-4 text-cyan-600" />行业板块分析
              </h3>
              <p className="text-xs text-slate-400">按涨跌幅排序，展示各行业板块的涨跌情况、领涨股及资金流向（数据源：同花顺）</p>
            </div>
            <IndustryTable data={sectorData} loading={sectorLoading} error={sectorError} />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
              <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-700">
                <Globe className="h-4 w-4 text-cyan-600" />概念板块分析
              </h3>
              <p className="text-xs text-slate-400">按涨跌幅排序，展示各概念/题材板块的资金动向（数据源：东方财富板块异动）</p>
            </div>
            <ConceptTable data={sectorData} loading={sectorLoading} error={sectorError} />
          </div>
        )}
      </main>
    </div>
  );
};

export default MarketAnalysisPage;
