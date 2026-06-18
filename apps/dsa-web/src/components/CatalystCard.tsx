import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronRight, Zap } from 'lucide-react';
import { type CatalystAnalysis } from '../api/business';
import { cn } from '../utils/cn';

const CONFIDENCE_COLORS: Record<string, string> = {
  '高': 'bg-emerald-50 text-emerald-700',
  '中': 'bg-amber-50 text-amber-700',
  '低': 'bg-red-50 text-red-700',
};

const IMPACT_COLORS: Record<string, string> = {
  '重大': 'bg-emerald-50 text-emerald-700',
  '中等': 'bg-amber-50 text-amber-700',
  '有限': 'bg-slate-100 text-slate-600',
};

const ASSESSMENT_STYLES: Record<string, string> = {
  '催化充分': 'border-emerald-200 bg-emerald-50',
  '催化一般': 'border-amber-200 bg-amber-50',
  '催化不足': 'border-red-200 bg-red-50',
};

const ASSESSMENT_TEXT: Record<string, string> = {
  '催化充分': 'text-emerald-700',
  '催化一般': 'text-amber-700',
  '催化不足': 'text-red-700',
};

const TYPE_ICONS: Record<string, string> = {
  '业绩催化': '📊',
  '政策催化': '📜',
  '事件催化': '🎯',
  '行业催化': '🏭',
  '资金催化': '💰',
};

export default function CatalystCard({ analysis }: { analysis: CatalystAnalysis }) {
  const [inputExpanded, setInputExpanded] = useState(false);

  // Fallback: raw text rendering
  if (!analysis.overall_assessment && analysis.raw_text) {
    return (
      <div className="stock-analysis-panel">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Zap className="h-4 w-4 text-amber-500" />催化分析
          {analysis.model && (
            <span className="ml-auto text-xs font-normal text-slate-400">
              {analysis.model.replace('openai/', '')}
            </span>
          )}
        </h3>
        <div className="prose prose-slate prose-sm max-w-none
          prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.raw_text}</ReactMarkdown>
        </div>
      </div>
    );
  }

  if (!analysis.overall_assessment) return null;

  const assessStyle = ASSESSMENT_STYLES[analysis.overall_assessment] || 'border-slate-200 bg-slate-50';
  const assessText = ASSESSMENT_TEXT[analysis.overall_assessment] || 'text-slate-700';

  return (
    <div className="stock-analysis-panel">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Zap className="h-4 w-4 text-amber-500" />催化分析
        {analysis.model && (
          <span className="ml-auto text-xs font-normal text-slate-400">
            {analysis.model.replace('openai/', '')}
          </span>
        )}
      </h3>

      {/* Overall assessment banner */}
      <div className={cn('mb-4 rounded-lg border px-3 py-2', assessStyle)}>
        <div className="flex items-center gap-2">
          <span className={cn('text-sm font-semibold', assessText)}>
            {analysis.overall_assessment}
          </span>
          {analysis.summary && (
            <span className="text-sm text-slate-600">— {analysis.summary}</span>
          )}
        </div>
      </div>

      {/* Catalyst items */}
      {analysis.catalysts?.length > 0 && (
        <div className="space-y-2.5 mb-4">
          {analysis.catalysts.map((cat, i) => {
            const icon = TYPE_ICONS[cat.type] || '📌';
            const confColor = CONFIDENCE_COLORS[cat.confidence] || 'bg-slate-100 text-slate-600';
            const impColor = IMPACT_COLORS[cat.impact] || 'bg-slate-100 text-slate-600';
            return (
              <div key={i} className="border-b border-slate-50 pb-2.5 last:border-0 last:pb-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm">{icon}</span>
                  <span className="text-sm font-medium text-slate-700">{cat.type}</span>
                  <span className={cn('ml-auto rounded-full px-2 py-0.5 text-xs font-medium', confColor)}>
                    {cat.confidence}
                  </span>
                  <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', impColor)}>
                    {cat.impact}
                  </span>
                </div>
                <p className="text-sm leading-relaxed text-slate-600">{cat.description}</p>
                {cat.timeframe && (
                  <p className="mt-1 text-xs text-slate-400">⏰ {cat.timeframe}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Key dates */}
      {analysis.key_dates?.length > 0 && (
        <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2">
          <p className="text-xs font-medium text-slate-400 mb-1.5">📅 关键时间窗口</p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.key_dates.map((date, i) => (
              <span key={i} className="rounded bg-white px-2 py-0.5 text-xs text-slate-600 border border-slate-200">
                {date}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Risks */}
      {analysis.risks?.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-slate-400 mb-1.5">⚠️ 催化落空风险</p>
          <ul className="space-y-1">
            {analysis.risks.map((risk, i) => (
              <li key={i} className="text-xs text-slate-500 flex items-start gap-1.5">
                <span className="mt-0.5 text-red-400 shrink-0">•</span>
                {risk}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Collapsible LLM input */}
      {analysis.llm_input && (
        <div className="border-t border-slate-100 pt-3">
          <button
            type="button"
            onClick={() => setInputExpanded(!inputExpanded)}
            className="flex w-full items-center gap-1.5 text-left text-xs text-slate-400 transition-colors hover:text-slate-600"
          >
            <ChevronRight className={cn('h-3 w-3 transition-transform', inputExpanded && 'rotate-90')} />
            分析输入数据
          </button>
          {inputExpanded && (
            <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-500 whitespace-pre-wrap">
              {analysis.llm_input}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
