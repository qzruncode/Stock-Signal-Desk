import { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity,
  CheckCircle2,
  Clock,
  Globe,
  LoaderCircle,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { useBusinessStream } from '../../hooks';
import { BusinessAnalysisPanel } from './BusinessAnalysisPanel';

export function BusinessAnalysisPanelStreaming({ symbol }: { symbol: string }) {
  const {
    phase,
    progressEvents,
    streamingText,
    envStreamingText,
    trackStreamingText,
    catalystStreamingText,
    business,
    isCached,
    error,
    startStream,
  } = useBusinessStream();

  useEffect(() => {
    if (symbol) startStream(symbol);
  }, [symbol]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div className="rounded-2xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center">
        <p className="text-sm font-medium text-red-600">{error}</p>
      </div>
    );
  }

  if (phase === 'connecting' || phase === 'fetching') {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
          正在获取业务数据（含AI分析，请耐心等待）...
        </div>
        {progressEvents.length > 0 && (
          <div className="stock-analysis-panel">
            <div className="space-y-2">
              {progressEvents.map((p, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="text-slate-600">{p.label}</span>
                  <span className="ml-auto text-slate-400">{p.step}/{p.total}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (phase === 'analyzing' || (phase === 'done' && !business)) {
    if (business) {
      const catalystCard = catalystStreamingText ? (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Zap className="h-4 w-4 text-amber-500" />催化分析
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-amber-400" />
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{catalystStreamingText}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className="stock-analysis-panel">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-amber-500" />
            AI 正在分析催化因素...
          </div>
        </div>
      );

      const trackCard = trackStreamingText ? (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <TrendingUp className="h-4 w-4 text-cyan-600" />赛道质量评估
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-400" />
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{trackStreamingText}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className="stock-analysis-panel">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
            AI 正在评估赛道质量...
          </div>
        </div>
      );

      const envCard = envStreamingText ? (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Globe className="h-4 w-4 text-cyan-600" />外部环境分析
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-400" />
          </h3>
          <div className="prose prose-slate prose-sm max-w-none
            prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{envStreamingText}</ReactMarkdown>
          </div>
        </div>
      ) : !trackStreamingText ? (
        <div className="stock-analysis-panel">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
            AI 正在分析外部环境...
          </div>
        </div>
      ) : null;

      return (
        <div className="space-y-6">
          {catalystCard}
          {trackCard}
          {envCard}
          {business.llm_analysis?.llm_used && business.llm_analysis?.analysis && (
            <div className="stock-analysis-panel">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
                <Activity className="h-4 w-4 text-cyan-600" />业务动向分析
              </h3>
              <div className="prose prose-slate prose-sm max-w-none
                prose-headings:text-slate-800 prose-headings:font-semibold
                prose-h3:text-sm prose-h3:mt-4 prose-h3:mb-2
                prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600
                prose-strong:text-slate-800 prose-strong:font-semibold
                prose-li:text-sm prose-li:text-slate-600
                prose-ul:space-y-1">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {business.llm_analysis.analysis}
                </ReactMarkdown>
              </div>
            </div>
          )}
          <BusinessAnalysisPanel business={{ ...business, llm_analysis: { llm_used: false }, environment_analysis: undefined, track_quality: undefined, catalyst_analysis: undefined }} />
        </div>
      );
    }

    return (
      <div className="space-y-6">
        {progressEvents.length > 0 && (
          <div className="stock-analysis-panel">
            <div className="space-y-2">
              {progressEvents.map((p, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="text-slate-600">{p.label}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {streamingText && (
          <div className="stock-analysis-panel">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Activity className="h-4 w-4 text-cyan-600" />业务动向分析
              {phase === 'analyzing' && <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-400" />}
            </h3>
            <div className="prose prose-slate prose-sm max-w-none
              prose-headings:text-slate-800 prose-headings:font-semibold
              prose-h3:text-sm prose-h3:mt-4 prose-h3:mb-2
              prose-p:text-sm prose-p:leading-relaxed prose-p:text-slate-600
              prose-strong:text-slate-800 prose-strong:font-semibold
              prose-li:text-sm prose-li:text-slate-600
              prose-ul:space-y-1">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {streamingText}
              </ReactMarkdown>
            </div>
          </div>
        )}
        {!streamingText && phase === 'analyzing' && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-500" />
            AI 正在分析业务动向...
          </div>
        )}
      </div>
    );
  }

  if (business) {
    return (
      <>
        <BusinessAnalysisPanel business={business} />
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
          <Clock className="h-3 w-3" />
          <span>
            数据获取时间: {business._fetched_at ? new Date(business._fetched_at).toLocaleString('zh-CN') : '-'}
            {isCached || business._cached ? ' · 缓存' : ' · 实时'}
          </span>
        </div>
      </>
    );
  }

  return null;
}
