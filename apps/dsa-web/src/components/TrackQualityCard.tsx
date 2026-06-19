import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronRight, TrendingUp } from 'lucide-react';
import { type TrackQualityAnalysis } from '../api/business';
import { cn } from '../utils/cn';

const TRACK_DIMENSIONS = [
  { key: 'cycle_position' as const, icon: '🔄', label: '行业周期' },
  { key: 'growth_potential' as const, icon: '🚀', label: '未来空间' },
  { key: 'competition_intensity' as const, icon: '⚔️', label: '竞争格局' },
];

const VERDICT_STYLES: Record<string, string> = {
  '上升期': 'bg-emerald-50 text-emerald-700',
  '空间明确': 'bg-emerald-50 text-emerald-700',
  '格局良好': 'bg-emerald-50 text-emerald-700',
  '平稳期': 'bg-amber-50 text-amber-700',
  '增长一般': 'bg-amber-50 text-amber-700',
  '竞争一般': 'bg-amber-50 text-amber-700',
  '下行期': 'bg-red-50 text-red-700',
  '空间有限': 'bg-red-50 text-red-700',
  '严重内卷': 'bg-red-50 text-red-700',
};

const DEFAULT_STYLE = 'bg-slate-100 text-slate-500';

function TrendCell({ value, suffix = '%' }: { value: number | null | undefined; suffix?: string }) {
  if (value == null) return <span className="text-slate-400">-</span>;
  const isPositive = value > 0;
  const isNegative = value < 0;
  const color = isPositive ? 'text-emerald-600' : isNegative ? 'text-red-600' : 'text-slate-600';
  return (
    <span className={cn('tabular-nums', color)}>
      {isPositive ? '+' : ''}{value.toFixed(1)}{suffix}
    </span>
  );
}

function MarginTrend({ trend }: { trend: string | undefined }) {
  if (!trend) return <span className="text-slate-400">-</span>;
  const color = trend === '上升' ? 'text-emerald-600' : trend === '下降' ? 'text-red-600' : 'text-slate-600';
  return <span className={color}>{trend}</span>;
}

export default function TrackQualityCard({ analysis }: { analysis: TrackQualityAnalysis }) {
  const [inputExpanded, setInputExpanded] = useState(false);

  // Fallback: raw text rendering
  if (!analysis.cycle_position && analysis.raw_text) {
    return (
      <div className="stock-analysis-panel">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <TrendingUp className="h-4 w-4 text-cyan-600" />赛道质量评估
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

  if (!analysis.cycle_position) return null;

  return (
    <div className="stock-analysis-panel">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <TrendingUp className="h-4 w-4 text-cyan-600" />赛道质量评估
        {analysis.model && (
          <span className="ml-auto text-xs font-normal text-slate-400">
            {analysis.model.replace('openai/', '')}
          </span>
        )}
      </h3>

      {/* Overall verdict */}
      {analysis.overall_verdict && (
        <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2">
          <p className="text-xs font-medium text-slate-400 mb-1">综合判断</p>
          <p className="text-sm font-medium leading-relaxed text-slate-700">{analysis.overall_verdict}</p>
        </div>
      )}

      {/* 3 dimensions */}
      <div className="space-y-3">
        {TRACK_DIMENSIONS.map(({ key, icon, label }) => {
          const dim = analysis[key];
          if (!dim) return null;
          const style = VERDICT_STYLES[dim.verdict] || DEFAULT_STYLE;
          return (
            <div key={key} className="border-b border-slate-50 pb-3 last:border-0 last:pb-0">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-sm">{icon}</span>
                <span className="text-sm font-medium text-slate-700">{label}</span>
                <span className={cn('ml-auto rounded-full px-2 py-0.5 text-xs font-medium', style)}>
                  {dim.verdict}
                </span>
              </div>
              <p className="text-sm leading-relaxed text-slate-600">{dim.evidence}</p>
            </div>
          );
        })}
      </div>

      {/* Peer snapshot table */}
      {analysis.peer_snapshot?.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <p className="text-xs font-medium text-slate-400 mb-2">同行对比</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-100 text-slate-400">
                  <th className="pb-1.5 text-left font-medium">公司</th>
                  <th className="pb-1.5 text-right font-medium">营收增速</th>
                  <th className="pb-1.5 text-right font-medium">毛利率趋势</th>
                  <th className="pb-1.5 text-right font-medium">净利润增速</th>
                </tr>
              </thead>
              <tbody>
                {analysis.peer_snapshot.map((peer, i) => (
                  <tr key={peer.symbol || i} className="border-b border-slate-50">
                    <td className="py-1.5 text-slate-700 font-medium">{peer.name}</td>
                    <td className="py-1.5 text-right"><TrendCell value={peer.revenue_growth} /></td>
                    <td className="py-1.5 text-right"><MarginTrend trend={peer.gross_margin_trend} /></td>
                    <td className="py-1.5 text-right"><TrendCell value={peer.net_profit_growth} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
