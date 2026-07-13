import { useCallback, useEffect, useRef, useState } from 'react';
import { toolRegistryApi } from '../api/toolRegistry';
import type { ToolExecuteResult, ToolMeta } from '../types/toolRegistry';
import { buildArguments, initialParamValue } from '../utils/toolTestParams';

export type ToolTestStatus = 'idle' | 'running' | 'success' | 'error';

export interface UseToolTestResult {
  /** 各参数的表单值(字符串形式)。 */
  values: Record<string, string>;
  setValue: (name: string, value: string) => void;
  status: ToolTestStatus;
  result: ToolExecuteResult | null;
  /** 失败时的可读错误信息(优先取响应 error,其次异常 message)。 */
  error: string | null;
  /** 前端弱校验错误(必填未填等),不触发后端调用。 */
  formError: string | null;
  run: () => void;
  reset: () => void;
}

/**
 * 单个工具的试运行状态机 + 参数表单值。
 * 每个 ToolListItem 各自持有一个 instance,状态天然按卡片隔离。
 * 组件卸载时忽略过期响应(对齐 useToolRegistry 写法)。
 */
export function useToolTest(tool: ToolMeta): UseToolTestResult {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const p of tool.parameters) {
      init[p.name] = initialParamValue(p);
    }
    return init;
  });
  const [status, setStatus] = useState<ToolTestStatus>('idle');
  const [result, setResult] = useState<ToolExecuteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;
    return () => {
      activeRef.current = false;
    };
  }, []);

  const setValue = useCallback((name: string, value: string) => {
    setValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  const run = useCallback(async () => {
    const { args, missingRequired } = buildArguments(tool, values);
    if (missingRequired.length > 0) {
      setFormError(`必填参数未填: ${missingRequired.join(', ')}`);
      return;
    }
    setFormError(null);
    setStatus('running');
    setError(null);
    try {
      const resp = await toolRegistryApi.runTool(tool.name, args);
      if (!activeRef.current) return;
      setResult(resp);
      if (resp.success) {
        setStatus('success');
        setError(null);
      } else {
        setStatus('error');
        setError(resp.error ?? '工具执行失败');
      }
    } catch (err: unknown) {
      if (!activeRef.current) return;
      setStatus('error');
      setError(err instanceof Error ? err.message : '工具执行失败');
    }
  }, [tool, values]);

  const reset = useCallback(() => {
    setStatus('idle');
    setResult(null);
    setError(null);
    setFormError(null);
  }, []);

  return { values, setValue, status, result, error, formError, run, reset };
}

export default useToolTest;
