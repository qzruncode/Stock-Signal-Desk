import { useCallback, useEffect, useMemo, useState } from 'react';
import { toolRegistryApi } from '../api/toolRegistry';
import type { ToolRegistryResponse } from '../types/toolRegistry';

export type ToolRegistryStatus = 'loading' | 'ready' | 'error';

export interface UseToolRegistryResult {
  status: ToolRegistryStatus;
  data: ToolRegistryResponse | null;
  error: string | null;
  refetch: () => void;
}

/**
 * 拉取 /api/v1/agent/tool-registry 全部 tool 元数据。
 */
export function useToolRegistry(): UseToolRegistryResult {
  const [data, setData] = useState<ToolRegistryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState(0);

  const status: ToolRegistryStatus = useMemo(() => {
    if (error !== null) return 'error';
    if (data === null) return 'loading';
    return 'ready';
  }, [data, error]);

  useEffect(() => {
    let active = true;
    toolRegistryApi
      .listTools()
      .then((resp) => {
        if (!active) return;
        setData(resp);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!active) return;
        const message = err instanceof Error ? err.message : '加载工具列表失败';
        setError(message);
        setData(null);
      });
    return () => {
      active = false;
    };
  }, [pendingKey]);

  const refetch = useCallback(() => {
    setPendingKey((value) => value + 1);
  }, []);

  return { status, data, error, refetch };
}

export default useToolRegistry;