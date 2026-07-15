import { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Play } from 'lucide-react';
import { Badge, Button } from '../common';
import { useToolTest } from '../../hooks/useToolTest';

const RESULT_TRUNCATE_CHARS = 4000;

interface ToolTestPanelProps {
  /** 由 ToolListItem 提供的试运行状态(参数表单值也托管其中)。 */
  test: ReturnType<typeof useToolTest>;
}

/**
 * 工具试运行的「执行按钮 + 结果展示」区。
 * 参数输入框不在此处 —— 它们被嵌入 ToolListItem 的参数列表行内,避免参数信息重复展示。
 */
export const ToolTestPanel: React.FC<ToolTestPanelProps> = ({ test }) => {
  const { status, result, error, formError, run, reset } = test;
  const [expanded, setExpanded] = useState(false);
  const running = status === 'running';

  const jsonText = useMemo(() => {
    if (status === 'error') return error ?? '未知错误';
    if (result?.result === undefined) return '';
    try {
      return JSON.stringify(result.result, null, 2);
    } catch {
      return String(result.result);
    }
  }, [status, result, error]);

  const truncated = jsonText.length > RESULT_TRUNCATE_CHARS;
  const displayed = !expanded && truncated ? jsonText.slice(0, RESULT_TRUNCATE_CHARS) : jsonText;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={run}
          disabled={running}
          isLoading={running}
          loadingText="运行中…"
        >
          <Play className="h-3.5 w-3.5" />
          试运行
        </Button>
        {status !== 'idle' && !running ? (
          <Button size="sm" variant="ghost" onClick={reset}>
            清除
          </Button>
        ) : null}
      </div>

      {formError ? <p className="text-xs text-danger">{formError}</p> : null}

      {(status === 'success' || status === 'error') && result ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Badge variant={result.success ? 'success' : 'danger'} size="sm">
              {result.success ? '成功' : '失败'}
            </Badge>
            <span className="text-[11px] text-muted-foreground">⏱ {result.durationMs}ms</span>
          </div>
          {jsonText ? (
            <div className="rounded-md border border-border/40 bg-muted/40">
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words p-3 text-[11px] leading-relaxed text-foreground">
                {displayed}
                {!expanded && truncated ? '…' : ''}
              </pre>
              {truncated ? (
                <button
                  type="button"
                  onClick={() => setExpanded((v) => !v)}
                  className="flex w-full items-center justify-center gap-1 border-t border-border/40 py-1.5 text-[11px] text-cyan hover:bg-hover/40"
                >
                  {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  {expanded ? '收起' : `展开全部 (${jsonText.length} 字符)`}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

    </div>
  );
};

export default ToolTestPanel;
