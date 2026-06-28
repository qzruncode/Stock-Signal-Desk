import { useCallback, useState } from 'react';
import { stocksApi, type KlineStatusResponse } from '../api/stocks';

export function useVerifyModal() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<KlineStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const show = useCallback(async () => {
    setOpen(true);
    setLoading(true);
    try {
      const result = await stocksApi.getKlineStatus();
      setData(result);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const hide = useCallback(() => {
    setOpen(false);
    setData(null);
  }, []);

  return { open, data, loading, show, hide };
}