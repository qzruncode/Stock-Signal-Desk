import { useCallback, useState } from 'react';
import { klineApi, type KlineResponse } from '../api/kline';

export interface KlineModalState {
  stock: { code: string; name: string } | null;
  data: KlineResponse | null;
  loading: boolean;
  error: string | null;
}

export function useKlineModal() {
  const [stock, setStock] = useState<{ code: string; name: string } | null>(null);
  const [data, setData] = useState<KlineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback(async (target: { code: string; name: string }) => {
    setStock(target);
    setData(null);
    setError(null);
    setLoading(true);
    try {
      const result = await klineApi.getKline(target.code, 250);
      setData(result);
    } catch {
      setData(null);
      setError('获取 K 线数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const close = useCallback(() => {
    setStock(null);
    setData(null);
    setLoading(false);
    setError(null);
  }, []);

  return { stock, data, loading, error, open, close };
}