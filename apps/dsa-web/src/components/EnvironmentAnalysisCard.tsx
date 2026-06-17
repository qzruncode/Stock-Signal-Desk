import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronRight, Globe } from 'lucide-react';
import { type EnvironmentAnalysis } from '../api/business';
import { cn } from '../utils/cn';

const ENV_DIMENSIONS = [
  { key: 'policy' as const, icon: '🏛', label: '政策环境' },
  { key: 'technology' as const, icon: '🔬', label: '技术变革' },
  { key: 'demand' as const, icon: '📈', label: '需求变化' },
  { key: 'supply_competition' as const, icon: '🏭', label: '供给与竞争' },
];

const SIGNAL_STYLES: Record<string, string> = {
  '利好': 'bg-emerald-50 text-emerald-700',
  '利空': 'bg-red-50 text-red-700',
  '中性': 'bg-slate-100 text-slate-500',
};

export default function EnvironmentAnalysisCard({ analysis }: { analysis: EnvironmentAnalysis }) {
  const [inputExpanded, setInputExpanded] = useState(false);

  // If JSON parse failed and we have raw_text, render as markdown
  if (!analysis.policy && analysis.raw_text) {
    return (
      <div className="stock-analysis-panel">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Globe className="h-4 w-4 text-cyan-600" />外部环境分析
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

  if (!analysis.policy) return null;

  return (
    <div className="stock-analysis-panel">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Globe className="h-4 w-4 text-cyan-600" />外部环境分析
        {analysis.model && (
          <span className="ml-auto text-xs font-normal text-slate-400">
            {analysis.model.replace('openai/', '')}
          </span>
        )}
      </h3>

      {/* Macro context */}
      {analysis.macro_context && (
        <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2">
          <p className="text-xs font-medium text-slate-400 mb-1">宏观背景</p>
          <p className="text-sm leading-relaxed text-slate-600">{analysis.macro_context}</p>
        </div>
      )}

      {/* 4 dimensions */}
      <div className="space-y-3">
        {ENV_DIMENSIONS.map(({ key, icon, label }) => {
          const dim = analysis[key];
          if (!dim) return null;
          const signalStyle = SIGNAL_STYLES[dim.signal] || SIGNAL_STYLES['中性'];
          return (
            <div key={key} className="border-b border-slate-50 pb-3 last:border-0 last:pb-0">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-sm">{icon}</span>
                <span className="text-sm font-medium text-slate-700">{label}</span>
                <span className={cn('ml-auto rounded-full px-2 py-0.5 text-xs font-medium', signalStyle)}>
                  {dim.signal}
                </span>
              </div>
              <p className="text-sm leading-relaxed text-slate-600">{dim.summary}</p>
              {dim.factors?.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {dim.factors.map((f, i) => (
                    <span key={i} className="rounded bg-slate-50 px-1.5 py-0.5 text-xs text-slate-500">
                      {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Collapsible LLM input */}
      {analysis.llm_input && (
        <div className="mt-4 border-t border-slate-100 pt-3">
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
