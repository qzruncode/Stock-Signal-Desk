import { useCallback, useEffect, useRef, useState } from 'react';
import { macroApi } from '../api/macro';
import type {
  IndexDataResponse,
  BondYieldResponse,
  IndicatorResponse,
  SectorFlowResponse,
  MarketBreadthResponse,
} from '../types/macro';
import { INDICATOR_OPTIONS } from '../types/macro';

export type MacroDimension =
  | 'index'
  | 'bond'
  | 'indicator'
  | 'sector_flow'
  | 'market_breadth';

export interface UseMacroDataParams {
  dimension: MacroDimension;
  // Index
  indexCode: string;
  days: number;
  // Bond
  bondCountry: string;
  bondTerm: string;
  // Indicator
  indicatorMonths: number;
  // Sector flow
  sectorFlowType: string;
}

export interface UseMacroDataResult {
  // Index
  indexData: IndexDataResponse | null;
  indexLoading: boolean;
  indexError: string | null;
  // Bond
  bondData: BondYieldResponse | null;
  bondLoading: boolean;
  bondError: string | null;
  // Indicator
  indicators: Record<string, IndicatorResponse>;
  indicatorsLoading: boolean;
  indicatorsError: string | null;
  // Sector flow
  sectorFlowData: SectorFlowResponse | null;
  sectorFlowLoading: boolean;
  sectorFlowError: string | null;
  // Market breadth
  marketBreadthData: MarketBreadthResponse | null;
  marketBreadthLoading: boolean;
  marketBreadthError: string | null;
  // Fetch triggers
  fetchIndexData: () => Promise<void>;
  fetchBondYield: () => Promise<void>;
  fetchAllIndicators: () => Promise<void>;
  fetchSectorFlow: () => Promise<void>;
  fetchMarketBreadth: () => Promise<void>;
}

/**
 * Extracts the abort-controller ref, 5 dimension-conditional fetch callbacks,
 * and the 5 dimension-conditional `useEffect`s from `MacroDataPage` into a
 * reusable data-only hook. Component rendering stays on the page; this hook
 * owns the data lifecycle.
 */
export function useMacroData(params: UseMacroDataParams): UseMacroDataResult {
  const {
    dimension,
    indexCode,
    days,
    bondCountry,
    bondTerm,
    indicatorMonths,
    sectorFlowType,
  } = params;

  // Index data
  const [indexData, setIndexData] = useState<IndexDataResponse | null>(null);
  const [indexLoading, setIndexLoading] = useState(false);
  const [indexError, setIndexError] = useState<string | null>(null);

  // Bond yield
  const [bondData, setBondData] = useState<BondYieldResponse | null>(null);
  const [bondLoading, setBondLoading] = useState(false);
  const [bondError, setBondError] = useState<string | null>(null);

  // Macro indicator
  const [indicators, setIndicators] = useState<Record<string, IndicatorResponse>>({});
  const [indicatorsLoading, setIndicatorsLoading] = useState(false);
  const [indicatorsError, setIndicatorsError] = useState<string | null>(null);

  // Sector fund flow
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

  useEffect(() => () => {
    abortRef.current?.abort();
  }, []);

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
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message
        || (err as Error)?.message
        || '获取数据失败';
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
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message
        || (err as Error)?.message
        || '获取数据失败';
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
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message
        || (err as Error)?.message
        || '获取数据失败';
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
      const msg = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message
        || (err as Error)?.message
        || '获取数据失败';
      setMarketBreadthError(msg);
      setMarketBreadthData(null);
    } finally {
      setMarketBreadthLoading(false);
    }
  }, [getSignal]);

  // Index auto-fetches on mount and when its inputs change (mirrors the page).
  useEffect(() => {
    // Data-loading effect (async fetch setStates after await, not synchronously).
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

  return {
    indexData,
    indexLoading,
    indexError,
    bondData,
    bondLoading,
    bondError,
    indicators,
    indicatorsLoading,
    indicatorsError,
    sectorFlowData,
    sectorFlowLoading,
    sectorFlowError,
    marketBreadthData,
    marketBreadthLoading,
    marketBreadthError,
    fetchIndexData,
    fetchBondYield,
    fetchAllIndicators,
    fetchSectorFlow,
    fetchMarketBreadth,
  };
}

export default useMacroData;
