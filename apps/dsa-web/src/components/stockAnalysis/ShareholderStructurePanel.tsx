import { Users } from 'lucide-react';
import {
  formatPctValue,
  formatShares,
} from '../../utils/stockAnalysisFormat';
import type { ShareholderStructureResponse } from '../../api/shareholder';
import { DataItem } from './DataItem';

export function ShareholderStructurePanel({ shareholder }: { shareholder: ShareholderStructureResponse }) {
  const countChange = shareholder.holder_count_change_pct ?? 0;
  const changeUp = countChange > 0;
  const changeDown = countChange < 0;

  return (
    <div className="stock-analysis-panel">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
        <Users className="h-4 w-4 text-cyan-600" />股东结构
        {shareholder.holder_report_date && (
          <span className="ml-auto text-xs font-normal text-slate-400">{shareholder.holder_report_date}</span>
        )}
      </h3>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <DataItem label="股东人数" value={shareholder.holder_count != null ? shareholder.holder_count.toLocaleString('zh-CN') : '-'} highlight />
        <DataItem
          label="环比变化"
          value={formatPctValue(shareholder.holder_count_change_pct)}
          highlightUp={changeUp}
          highlightDown={changeDown}
        />
        <DataItem label="机构持股" value={formatPctValue(shareholder.institution_holding_pct)} />
        <DataItem label="实际控制人" value={shareholder.actual_controller || '-'} />
      </div>

      {shareholder.top10_holders.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] table-fixed">
              <colgroup>
                <col className="w-[48%]" />
                <col className="w-[14%]" />
                <col className="w-[20%]" />
                <col className="w-[18%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-100 text-xs text-slate-400">
                  <th className="pb-2 text-left font-medium">股东名称</th>
                  <th className="pb-2 text-right font-medium">持股比例</th>
                  <th className="border-r border-slate-100 pb-2 pr-6 text-right font-medium">持股数量</th>
                  <th className="pb-2 pl-6 text-left font-medium">性质</th>
                </tr>
              </thead>
              <tbody>
                {shareholder.top10_holders.slice(0, 10).map((holder, index) => (
                  <tr key={`${holder.name}-${index}`} className="border-b border-slate-50 text-xs">
                    <td className="py-2 pr-4 text-slate-700">{holder.name || '-'}</td>
                    <td className="py-2 text-right tabular-nums text-slate-700">{formatPctValue(holder.holding_pct)}</td>
                    <td className="border-r border-slate-100 py-2 pr-6 text-right tabular-nums text-slate-600">{formatShares(holder.holding_amount)}</td>
                    <td className="py-2 pl-6 text-slate-500">{holder.holder_type || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {shareholder.major_holder_changes.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="mb-2 text-xs font-medium text-slate-400">近期大股东增减持</p>
          <div className="grid gap-2">
            {shareholder.major_holder_changes.slice(0, 8).map((item, index) => (
              <div key={`${item.date}-${item.holder}-${index}`} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-3 text-xs">
                <span className="text-slate-400">{item.date || '-'}</span>
                <span className="truncate text-slate-700">{item.holder || '-'}</span>
                <span className="tabular-nums text-slate-500">
                  {item.direction || '-'} {formatShares(item.shares)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
