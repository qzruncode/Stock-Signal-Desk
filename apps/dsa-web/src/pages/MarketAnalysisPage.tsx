import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Globe, Layers } from 'lucide-react';
import { Select } from '../components/common';
import { marketApi, type MarketStatus } from '../api/market';
import { sectorsApi, type SectorListResponse, type SectorType } from '../api/sectors';
import MarketOverviewPanel from '../components/MarketOverviewPanel';
import SectorTable, {
  type SectorColumn,
} from '../components/market/SectorTable';
import {
  changeTextColor,
  formatFlow,
  formatPct,
  type SectorSortField,
} from '../utils/sectorFormat';
import { cn } from '../utils/cn';

type AnalysisMode = 'market' | 'industry' | 'concept';

const INDUSTRY_COLUMNS: SectorColumn[] = [
  {
    field: 'name',
    label: '行业',
    render: (item) => <span className="font-medium text-slate-800">{item.name}</span>,
  },
  {
    field: 'change_pct',
    label: '涨跌幅',
    align: 'right',
    render: (item) => (
      <span className={cn('font-semibold tabular-nums', changeTextColor(item.change_pct, true))}>
        {formatPct(item.change_pct)}
      </span>
    ),
  },
  {
    label: '领涨股',
    render: (item) => item.lead_stock ? (
      <span className="text-slate-600">
        {item.lead_stock}
        {item.lead_stock_change_pct != null ? (
          <span className={cn('ml-1 text-xs', changeTextColor(item.lead_stock_change_pct))}>
            {formatPct(item.lead_stock_change_pct)}
          </span>
        ) : null}
      </span>
    ) : '-',
  },
  {
    field: 'up_count',
    label: '上涨',
    align: 'right',
    render: (item) => <span className="tabular-nums text-red-500">{item.up_count ?? '-'}</span>,
  },
  {
    field: 'down_count',
    label: '下跌',
    align: 'right',
    render: (item) => <span className="tabular-nums text-green-500">{item.down_count ?? '-'}</span>,
  },
  {
    field: 'net_flow',
    label: '净流入',
    align: 'right',
    render: (item) => (
      <span className={cn('tabular-nums', changeTextColor(item.net_flow))}>
        {formatFlow(item.net_flow)}
      </span>
    ),
  },
];

const CONCEPT_COLUMNS: SectorColumn[] = [
  {
    field: 'name',
    label: '概念板块',
    render: (item) => (
      <>
        <span className="font-medium text-slate-800">{item.name}</span>
        {item.code ? <span className="ml-2 text-xs text-slate-400">{item.code}</span> : null}
      </>
    ),
  },
  {
    field: 'change_pct',
    label: '涨跌幅',
    align: 'right',
    render: (item) => (
      <span className={cn('font-semibold tabular-nums', changeTextColor(item.change_pct, true))}>
        {formatPct(item.change_pct)}
      </span>
    ),
  },
  {
    field: 'net_flow',
    label: '主力净流入',
    align: 'right',
    render: (item) => (
      <span className={cn('tabular-nums', changeTextColor(item.net_flow))}>
        {formatFlow(item.net_flow)}
      </span>
    ),
  },
];

function useSectorSort(initialField: SectorSortField = 'change_pct') {
  const [sortField, setSortField] = useState<SectorSortField>(initialField);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const handleSort = useCallback((field: SectorSortField) => {
    setSortField((currentField) => {
      if (currentField === field) {
        setSortDir((currentDir) => (currentDir === 'asc' ? 'desc' : 'asc'));
        return currentField;
      }
      setSortDir('desc');
      return field;
    });
  }, []);

  return { sortField, sortDir, handleSort };
}

const MarketAnalysisPage: React.FC = () => {
  const [marketData, setMarketData] = useState<MarketStatus | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState<string | null>(null);
  const [sectorData, setSectorData] = useState<SectorListResponse | null>(null);
  const [sectorLoading, setSectorLoading] = useState(false);
  const [sectorError, setSectorError] = useState<string | null>(null);
  const [mode, setMode] = useState<AnalysisMode>('market');
  const sectorSort = useSectorSort();

  const fetchMarketStatus = useCallback(async () => {
    setMarketLoading(true);
    setMarketError(null);
    try {
      setMarketData(await marketApi.getStatus());
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
      setSectorData(await sectorsApi.getSectors(sectorType));
    } catch {
      setSectorData(null);
      setSectorError('获取板块数据失败，请稍后重试');
    } finally {
      setSectorLoading(false);
    }
  }, []);

  useEffect(() => {
    if (mode === 'market') void fetchMarketStatus();
    else void fetchSectors(mode);
  }, [fetchMarketStatus, fetchSectors, mode]);

  const sectorConfig = useMemo(() => {
    if (mode === 'concept') {
      return {
        icon: <Globe className="h-4 w-4 text-cyan-600" />,
        title: '概念板块分析',
        description: '按涨跌幅排序，展示各概念/题材板块的资金动向（数据源：东方财富板块异动）',
        columns: CONCEPT_COLUMNS,
        emptyMessage: '暂无概念数据',
        loadingMessage: '正在获取概念数据...',
        footerLabel: '概念板块',
      };
    }
    return {
      icon: <Layers className="h-4 w-4 text-cyan-600" />,
      title: '行业板块分析',
      description: '按涨跌幅排序，展示各行业板块的涨跌情况、领涨股及资金流向（数据源：同花顺）',
      columns: INDUSTRY_COLUMNS,
      emptyMessage: '暂无行业数据',
      loadingMessage: '正在获取行业数据...',
      footerLabel: '行业',
    };
  }, [mode]);

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
            onChange={(value) => setMode(value as AnalysisMode)}
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
        ) : (
          <div className="space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white/88 p-5 shadow-sm">
              <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-slate-700">
                {sectorConfig.icon}
                {sectorConfig.title}
              </h3>
              <p className="text-xs text-slate-400">{sectorConfig.description}</p>
            </div>
            <SectorTable
              data={sectorData}
              loading={sectorLoading}
              error={sectorError}
              columns={sectorConfig.columns}
              sortField={sectorSort.sortField}
              sortDir={sectorSort.sortDir}
              onSort={sectorSort.handleSort}
              emptyMessage={sectorConfig.emptyMessage}
              loadingMessage={sectorConfig.loadingMessage}
              footerLabel={sectorConfig.footerLabel}
            />
          </div>
        )}
      </main>
    </div>
  );
};

export default MarketAnalysisPage;
