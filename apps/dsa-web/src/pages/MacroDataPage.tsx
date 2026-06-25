import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Select } from '../components/common';
import { macroApi } from '../api/macro';
import type {
  IndexDataResponse,
  BondYieldResponse,
  IndicatorResponse,
  SectorFlowResponse,
  MarketBreadthResponse,
} from '../types/macro';
import { INDICATOR_OPTIONS } from '../types/macro';
import IndexPanelContent from '../components/macroData/IndexPanelContent';
import BondPanelContent from '../components/macroData/BondPanelContent';
import IndicatorPanelContent from '../components/macroData/IndicatorPanelContent';
import SectorFlowPanelContent from '../components/macroData/SectorFlowPanelContent';
import MarketBreadthPanelContent from '../components/macroData/MarketBreadthPanelContent';

// ---- Page ----

type MacroDimension = 'index' | 'bond' | 'indicator' | 'sector_flow' | 'market_breadth';

const DIMENSION_OPTIONS: { value: MacroDimension; label: string }[] = [
  { value: 'index', label: '大盘指数' },
  { value: 'bond', label: '债券收益率' },
  { value: 'indicator', label: '宏观经济指标' },
  { value: 'sector_flow', label: '板块资金流向' },
  { value: 'market_breadth', label: '市场宽度' },
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

  // AbortController for cancelling in-flight requests on unmount or re-fetch
  const abortRef = useRef<AbortController | null>(null);
  const getSignal = useCallback(() => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    return ctrl.signal;
  }, []);

  useEffect(() => () => { abortRef.current?.abort(); }, []);

  const fetchIndexData = useCallback(async () => {
    const signal = getSignal();
    setIndexLoading(true);
    setIndexError(null);
    setIndexData(null);
    try {
      const result = await macroApi.getIndexData(indexCode, days, signal);
      setIndexData(result);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || (err as Error)?.message || '获取数据失败';
      setIndexError(msg);
      setIndexData(null);
    } finally {
      setIndexLoading(false);
    }
  }, [indexCode, days, getSignal]);

  const fetchBondYield = useCallback(async () => {
    const signal = getSignal();
    setBondLoading(true);
    setBondError(null);
    setBondData(null);
    try {
      const result = await macroApi.getBondYield(bondCountry, bondTerm, signal);
      setBondData(result);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || (err as Error)?.message || '获取数据失败';
      setBondError(msg);
      setBondData(null);
    } finally {
      setBondLoading(false);
    }
  }, [bondCountry, bondTerm, getSignal]);

  const fetchAllIndicators = useCallback(async () => {
    const signal = getSignal();
    setIndicatorsLoading(true);
    setIndicatorsError(null);
    const results: Record<string, IndicatorResponse> = {};
    await Promise.allSettled(
      Object.keys(INDICATOR_OPTIONS).map(async (key) => {
        try {
          const result = await macroApi.getIndicator(key, indicatorMonths, signal);
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
  }, [indicatorMonths, getSignal]);

  const fetchSectorFlow = useCallback(async () => {
    const signal = getSignal();
    setSectorFlowLoading(true);
    setSectorFlowError(null);
    setSectorFlowData(null);
    try {
      const result = await macroApi.getSectorFlow(sectorFlowType, 10, signal);
      setSectorFlowData(result);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || (err as Error)?.message || '获取数据失败';
      setSectorFlowError(msg);
      setSectorFlowData(null);
    } finally {
      setSectorFlowLoading(false);
    }
  }, [sectorFlowType, getSignal]);

  const fetchMarketBreadth = useCallback(async () => {
    const signal = getSignal();
    setMarketBreadthLoading(true);
    setMarketBreadthError(null);
    setMarketBreadthData(null);
    try {
      const result = await macroApi.getMarketBreadth(signal);
      setMarketBreadthData(result);
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message || (err as Error)?.message || '获取数据失败';
      setMarketBreadthError(msg);
      setMarketBreadthData(null);
    } finally {
      setMarketBreadthLoading(false);
    }
  }, [getSignal]);

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

  const renderContent = () => {
    switch (dimension) {
      case 'index':
        return (
          <IndexPanelContent
            indexData={indexData} loading={indexLoading} error={indexError}
            indexCode={indexCode} setIndexCode={setIndexCode}
            days={days} setDays={setDays}
          />
        );
      case 'bond':
        return (
          <BondPanelContent
            bondData={bondData} bondLoading={bondLoading} bondError={bondError}
            bondCountry={bondCountry} setBondCountry={setBondCountry}
            bondTerm={bondTerm} setBondTerm={setBondTerm}
          />
        );
      case 'indicator':
        return (
          <IndicatorPanelContent
            indicators={indicators} loading={indicatorsLoading} error={indicatorsError}
          />
        );
      case 'sector_flow':
        return (
          <SectorFlowPanelContent
            sectorFlowData={sectorFlowData} sectorFlowLoading={sectorFlowLoading} sectorFlowError={sectorFlowError}
            sectorFlowType={sectorFlowType} setSectorFlowType={setSectorFlowType}
          />
        );
      case 'market_breadth':
        return (
          <MarketBreadthPanelContent
            marketBreadthData={marketBreadthData} marketBreadthLoading={marketBreadthLoading} marketBreadthError={marketBreadthError}
          />
        );
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
