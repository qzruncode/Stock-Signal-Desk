import { useRef, useEffect, useState } from 'react';
import { useIndustryBetaStream, type AnalysisChatMessage } from '../../hooks/useIndustryBetaStream';
import { Sparkles, Loader2, CheckCircle2, AlertTriangle, Bot, Database, FileText, ShieldAlert, ChevronRight, ChevronDown } from 'lucide-react';
import { Button } from '../common/Button';
import { Badge } from '../common/Badge';

interface IndustryBetaChatProps {
  symbol: string;
  sessionId: string;
}

export function IndustryBetaChat({ symbol, sessionId }: IndustryBetaChatProps) {
  const { messages, isRunning, isCompleted, startAnalysis, error } = useIndustryBetaStream({
    sessionId,
    symbol,
  });

  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="flex flex-col rounded-2xl border border-slate-200 bg-white shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-slate-500" />
          <span className="text-sm font-medium text-slate-900">行业beta实时分析</span>
          {isCompleted && (
            <Badge variant="success" size="sm">已完成</Badge>
          )}
          {isRunning && (
            <Badge variant="info" size="sm">分析中</Badge>
          )}
        </div>
        {isRunning && <Loader2 className="h-4 w-4 animate-spin text-cyan-600" />}
        {isCompleted && <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
      </div>

      {/* Scrollable message area */}
      <div ref={scrollRef} className="flex max-h-[560px] flex-col gap-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !isRunning && !isCompleted && (
          <div className="flex flex-1 items-center justify-center py-8 text-center">
            <div className="flex flex-col items-center gap-3">
              <Database className="h-8 w-8 text-slate-300" />
              <div>
                <p className="text-sm font-medium text-slate-500">尚未开始分析</p>
                <p className="text-xs text-slate-400">点击下方按钮开始行业beta数据采集与模型研判</p>
              </div>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <ChatMessageBubble key={msg.id} message={msg} />
        ))}

        {isRunning && messages.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span>正在分析...</span>
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="border-t border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" />
            <span>{error}</span>
          </div>
        </div>
      )}

      {/* Action bar */}
      {!isRunning && (
        <div className="border-t border-slate-100 px-4 py-3">
          <Button
            onClick={() => void startAnalysis()}
            size="sm"
            variant="primary"
          >
            <Sparkles className="h-4 w-4" />
            {isCompleted ? '重新分析' : '开始分析'}
          </Button>
        </div>
      )}
    </div>
  );
}

function ChatMessageBubble({ message }: { message: AnalysisChatMessage }) {
  if (message.type === 'data_collection') {
    const detail = message.detail as Record<string, unknown> | undefined;
    const hasDetail = detail && Object.keys(detail).length > 0;

    if (!hasDetail) {
      return (
        <div className="flex items-start gap-2 text-xs text-slate-500">
          <div className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400" />
          <span>{message.source && <span className="font-medium text-slate-600">{message.source}：</span>}{message.message}</span>
        </div>
      );
    }

    return <ExpandableDataLog source={message.source || '未知'} message={message.message} detail={detail} />;
  }

  if (message.type === 'llm_streaming') {
    // Extract Chinese analysis text (everything before the JSON block)
    const jsonStart = message.streamText?.lastIndexOf('{');
    const analysisText = jsonStart !== -1 ? message.streamText?.slice(0, jsonStart) : message.streamText;

    return (
      <div className="flex gap-2">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-100 text-violet-600">
          <Bot className="h-3.5 w-3.5" />
        </div>
        <div className="flex-1 rounded-xl bg-slate-50 px-3 py-2.5">
          <div className="mb-1 flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-slate-400">模型分析</span>
          </div>
          <div className="text-sm leading-6 text-slate-700">
            {analysisText || <span className="text-slate-400">正在综合证据进行研判...</span>}
          </div>
        </div>
      </div>
    );
  }

  if (message.type === 'result') {
    return <ResultCard result={message.result ?? {}} />;
  }

  return null;
}

function ExpandableDataLog({ source, message, detail }: { source: string; message?: string; detail: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <button
      type="button"
      onClick={() => setExpanded(!expanded)}
      className="flex w-full items-start gap-2 text-xs text-slate-500 hover:text-slate-700 transition-colors"
    >
      {expanded ? (
        <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-slate-400" />
      ) : (
        <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-slate-400" />
      )}
      <div className="flex-1 text-left">
        {source && <span className="font-medium text-slate-600">{source}：</span>}
        <span>{message}</span>
        {expanded && (
          <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-50 p-2 font-mono text-[11px] text-slate-600">
            {JSON.stringify(detail, null, 2)}
          </pre>
        )}
      </div>
    </button>
  );
}

function ResultCard({ result }: { result: Record<string, unknown> }) {
  const detector = (result.industry_beta_detector ?? {}) as Record<string, unknown>;
  const passed = Boolean(detector.passed);
  const conclusion = (detector.conclusion ?? '') as string;
  const checklist = (detector.checklist ?? []) as Array<Record<string, unknown>>;
  const priceWar = (result.price_war_analysis ?? {}) as Record<string, unknown>;
  const driver = (result.driver_analysis ?? {}) as Record<string, unknown>;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      {/* Verdict */}
      <div className="flex items-center gap-2">
        {passed ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
        ) : (
          <AlertTriangle className="h-4 w-4 text-amber-600" />
        )}
        <span className="text-sm font-medium text-slate-900">
          行业beta判定 {passed ? '通过' : '未完全通过'}
        </span>
      </div>

      {/* Conclusion */}
      {conclusion && (
        <p className="mt-2 text-sm text-slate-600">{conclusion}</p>
      )}

      {/* Checklist */}
      {checklist.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <p className="text-xs font-medium text-slate-500">判定清单</p>
          {checklist.map((item, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              {item.passed ? (
                <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
              ) : (
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
              )}
              <span className="text-slate-700">{String(item.item ?? '')}</span>
              {item.reason ? (
                <span className="text-slate-400">— {String(item.reason).slice(0, 60)}</span>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {/* Price war */}
      {Object.keys(priceWar).length > 0 && (
        <div className="mt-3 flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs">
          <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0 text-slate-400" />
          <div>
            <span className="font-medium text-slate-600">价格战风险：</span>
            <span className="text-slate-700">
              {priceWar.detected ? '检测到' : '未检测到'}
            </span>
            {priceWar.severity != null && (
              <Badge variant={String(priceWar.severity) === 'high' ? 'danger' : String(priceWar.severity) === 'medium' ? 'warning' : 'info'} size="sm" className="ml-1">
                {String(priceWar.severity)}
              </Badge>
            )}
          </div>
        </div>
      )}

      {/* Driver summary */}
      {Object.keys(driver).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
          {(['policy', 'technology', 'demand', 'supply'] as const).map((key) => {
            const items = (driver[key] ?? []) as string[];
            if (items.length === 0) return null;
            const label = { policy: '政策', technology: '技术', demand: '需求', supply: '供给' }[key];
            return (
              <span key={key} className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-slate-600">
                <FileText className="h-3 w-3" />
                {label} ({items.length})
              </span>
            );
          })}
          {driver.sufficiency != null && (
            <Badge
              variant={String(driver.sufficiency) === 'sufficient' ? 'success' : String(driver.sufficiency) === 'partial' ? 'warning' : 'danger'}
              size="sm"
            >
              {String(driver.sufficiency) === 'sufficient' ? '驱动充分' : String(driver.sufficiency) === 'partial' ? '驱动部分' : '驱动不足'}
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}

