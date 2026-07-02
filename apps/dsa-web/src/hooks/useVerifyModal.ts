import { useCallback, useState } from 'react';
import { stocksApi, type KlineStatusResponse, type SyncStatusResponse } from '../api/stocks';

const isActiveStatus = (status?: SyncStatusResponse | null) =>
  status?.status === 'running' || status?.status === 'syncing_kline';

export function useVerifyModal() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<KlineStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncStatus, setSyncStatus] = useState<SyncStatusResponse | null>(null);

  const refresh = useCallback(async () => {
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

  const show = useCallback(async () => {
    setOpen(true);
    await refresh();
  }, [refresh]);

  const syncMissing = useCallback(async () => {
    if (!data?.missing_codes?.length || isActiveStatus(syncStatus)) return;
    const initial = await stocksApi.syncMissingKline(data.missing_codes);
    setSyncStatus(initial);

    const poll = window.setInterval(async () => {
      try {
        const status = await stocksApi.syncMissingKlineStatus();
        setSyncStatus(status);
        if (!isActiveStatus(status)) {
          window.clearInterval(poll);
          await refresh();
        }
      } catch {
        window.clearInterval(poll);
      }
    }, 2000);
  }, [data?.missing_codes, refresh, syncStatus]);

  const hide = useCallback(() => {
    setOpen(false);
    setData(null);
    setSyncStatus(null);
  }, []);

  return { open, data, loading, syncStatus, show, hide, syncMissing };
}
