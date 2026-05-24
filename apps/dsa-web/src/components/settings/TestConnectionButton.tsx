import React, { useCallback, useState } from 'react';
import { CheckCircle2, XCircle, Zap } from 'lucide-react';
import { systemConfigApi } from '../../api/systemConfig';
import type { TestLLMChannelResponse } from '../../types/systemConfig';

interface TestConnectionButtonProps {
  name: string;
  protocol: string;
  apiKey: string;
  baseUrl?: string;
  models: string[];
  disabled?: boolean;
}

export const TestConnectionButton: React.FC<TestConnectionButtonProps> = ({
  name,
  protocol,
  apiKey,
  baseUrl,
  models,
  disabled = false,
}) => {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestLLMChannelResponse | null>(null);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setResult(null);
    try {
      const res = await systemConfigApi.testLLMChannel({
        name,
        protocol,
        baseUrl: baseUrl || undefined,
        apiKey: apiKey || undefined,
        models: models.length > 0 ? models : [protocol === 'deepseek' ? 'deepseek-chat' : ''],
        enabled: true,
      });
      setResult(res);
    } catch (err: unknown) {
      setResult({
        success: false,
        message: err instanceof Error ? err.message : 'Connection test failed',
        error: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setTesting(false);
    }
  }, [name, protocol, apiKey, baseUrl, models]);

  const canTest = !disabled && !testing && apiKey.trim().length > 0 && models.length > 0;

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={handleTest}
        disabled={!canTest}
        className="inline-flex items-center gap-1.5 self-start rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-xs font-medium text-cyan transition hover:bg-cyan/20 disabled:cursor-not-allowed disabled:border-border/30 disabled:bg-muted/20 disabled:text-muted-text"
      >
        {testing ? (
          <>
            <svg className="h-3.5 w-3.5 animate-spin text-current" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            测试中...
          </>
        ) : (
          <>
            <Zap className="h-3.5 w-3.5" />
            测试连接
          </>
        )}
      </button>

      {result && (
        <div
          className={`rounded-xl border px-3 py-2 text-xs ${
            result.success
              ? 'border-success/30 bg-success/10 text-success'
              : 'border-danger/30 bg-danger/10 text-danger'
          }`}
        >
          <div className="flex items-center gap-1.5 font-medium">
            {result.success ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <XCircle className="h-3.5 w-3.5" />
            )}
            {result.success ? '连接成功' : '连接失败'}
          </div>
          <p className="mt-1 opacity-90">{result.message}</p>
          {result.latencyMs != null && (
            <p className="mt-0.5 opacity-75">延迟: {result.latencyMs}ms</p>
          )}
          {result.resolvedModel && (
            <p className="mt-0.5 opacity-75">模型: {result.resolvedModel}</p>
          )}
          {result.error && (
            <p className="mt-0.5 opacity-75">错误: {result.error}</p>
          )}
        </div>
      )}
    </div>
  );
};
