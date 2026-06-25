import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Activity, BarChart3, ChevronRight, FileText } from 'lucide-react';
import { cn } from '../../utils/cn';
import { formatAmount } from '../../utils/stockAnalysisFormat';
import type { BusinessResponse } from '../../api/business';
import EnvironmentAnalysisCard from '../EnvironmentAnalysisCard';
import TrackQualityCard from '../TrackQualityCard';
import CatalystCard from '../CatalystCard';

export function BusinessAnalysisPanel({ business }: { business: BusinessResponse }) {
  const [inputExpanded, setInputExpanded] = useState(false);
  const intro = business.intro;
  const composition = business.composition;

  const latestReportDate = composition.length > 0
    ? composition.reduce((max, item) => item.report_date > max ? item.report_date : max, '')
    : '';

  const latestComposition = composition.filter(item => item.report_date === latestReportDate);

  const byIndustry = latestComposition.filter(item => item.category_type === '按行业分类');
  const byProduct = latestComposition.filter(item => item.category_type === '按产品分类');
  const byRegion = latestComposition.filter(item => item.category_type === '按地区分类');

  return (
    <div className="space-y-6">
      {business.catalyst_analysis?.llm_used && (
        <CatalystCard analysis={business.catalyst_analysis} />
      )}

      {business.track_quality?.llm_used && (
        <TrackQualityCard analysis={business.track_quality} />
      )}

      {business.environment_analysis?.llm_used && (
        <EnvironmentAnalysisCard analysis={business.environment_analysis} />
      )}

      {business.llm_analysis?.llm_used && business.llm_analysis?.analysis && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Activity className="h-4 w-4 text-cyan-600" />业务动向分析
            {business.llm_analysis.model && (
              <span className="ml-auto text-xs font-normal text-slate-400">
                {business.llm_analysis.model.replace('openai/', '')}
              </span>
            )}
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
          {business.llm_analysis.llm_input && (
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
                  {business.llm_analysis.llm_input}
                </pre>
              )}
            </div>
          )}
        </div>
      )}

      {(intro.main_business || intro.business_scope || intro.product_type || intro.product_name) && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <FileText className="h-4 w-4 text-cyan-600" />业务概况
          </h3>
          <div className="space-y-3">
            {intro.main_business && (
              <div>
                <p className="text-xs font-medium text-slate-400">主营业务</p>
                <p className="text-sm leading-relaxed text-slate-600">{intro.main_business}</p>
              </div>
            )}
            {intro.product_type && (
              <div>
                <p className="text-xs font-medium text-slate-400">产品类型</p>
                <p className="text-sm leading-relaxed text-slate-600">{intro.product_type}</p>
              </div>
            )}
            {intro.product_name && (
              <div>
                <p className="text-xs font-medium text-slate-400">产品名称</p>
                <p className="text-sm leading-relaxed text-slate-600">{intro.product_name}</p>
              </div>
            )}
            {intro.business_scope && (
              <div>
                <p className="text-xs font-medium text-slate-400">经营范围</p>
                <p className="text-sm leading-relaxed text-slate-600 line-clamp-4">{intro.business_scope}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {byIndustry.length > 0 && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />主营构成 — 按行业
            {latestReportDate && (
              <span className="ml-auto text-xs font-normal text-slate-400">{latestReportDate}</span>
            )}
          </h3>
          <div className="space-y-2">
            {byIndustry.map((item, index) => (
              <div key={`${item.business_name}-${index}`} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 border-b border-slate-50 pb-2 text-xs">
                <span className="font-medium text-slate-700">{item.business_name}</span>
                <span className="tabular-nums text-slate-600">
                  {item.revenue != null ? formatAmount(item.revenue) : '-'}
                </span>
                <span className="tabular-nums font-semibold text-slate-900">
                  {item.revenue_pct != null ? `${(item.revenue_pct * 100).toFixed(2)}%` : '-'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {byProduct.length > 0 && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />主营构成 — 按产品
            {latestReportDate && (
              <span className="ml-auto text-xs font-normal text-slate-400">{latestReportDate}</span>
            )}
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[48rem] table-fixed">
              <colgroup>
                <col className="w-[30%]" />
                <col className="w-[18%]" />
                <col className="w-[18%]" />
                <col className="w-[18%]" />
                <col className="w-[16%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-100 text-xs text-slate-400">
                  <th className="pb-2 text-left font-medium">产品</th>
                  <th className="pb-2 text-right font-medium">收入</th>
                  <th className="pb-2 text-right font-medium">收入占比</th>
                  <th className="pb-2 text-right font-medium">毛利率</th>
                </tr>
              </thead>
              <tbody>
                {byProduct.map((item, index) => (
                  <tr key={`${item.business_name}-${index}`} className="border-b border-slate-50 text-xs">
                    <td className="py-2 pr-4 font-medium text-slate-700">{item.business_name}</td>
                    <td className="py-2 text-right tabular-nums text-slate-600">
                      {item.revenue != null ? formatAmount(item.revenue) : '-'}
                    </td>
                    <td className="py-2 text-right tabular-nums font-semibold text-slate-900">
                      {item.revenue_pct != null ? `${(item.revenue_pct * 100).toFixed(2)}%` : '-'}
                    </td>
                    <td className="py-2 text-right tabular-nums text-slate-600">
                      {item.gross_margin != null ? `${(item.gross_margin * 100).toFixed(2)}%` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {byRegion.length > 0 && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />主营构成 — 按地区
            {latestReportDate && (
              <span className="ml-auto text-xs font-normal text-slate-400">{latestReportDate}</span>
            )}
          </h3>
          <div className="space-y-2">
            {byRegion.map((item, index) => (
              <div key={`${item.business_name}-${index}`} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 border-b border-slate-50 pb-2 text-xs">
                <span className="font-medium text-slate-700">{item.business_name}</span>
                <span className="tabular-nums text-slate-600">
                  {item.revenue != null ? formatAmount(item.revenue) : '-'}
                </span>
                <span className="tabular-nums font-semibold text-slate-900">
                  {item.revenue_pct != null ? `${(item.revenue_pct * 100).toFixed(2)}%` : '-'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
