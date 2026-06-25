import { BarChart3, Building2, FileText, Info, TrendingUp } from 'lucide-react';
import type { StockInfo } from '../../api/stockInfo';
import {
  formatAmount,
  formatMarketCap,
  formatShares,
} from '../../utils/stockAnalysisFormat';
import { DataItem } from './DataItem';

export function StockInfoPanel({ info }: { info: StockInfo }) {
  const hasEm = info._em_ok === true;

  return (
    <div className="space-y-4">
      <div className="stock-analysis-panel">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Building2 className="h-4 w-4 text-cyan-600" />公司概况
        </h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          <DataItem label="公司全称" value={info.name || info.short_name || '-'} />
          <DataItem label="所属行业" value={info.industry || '-'} />
          <DataItem label="所属市场" value={info.market || '-'} />
          <DataItem
            label="上市日期"
            value={info.listing_date ? info.listing_date.replace(/-/g, '/') : '-'}
          />
          <DataItem
            label="成立日期"
            value={info.establish_date ? info.establish_date.replace(/-/g, '/') : '-'}
          />
          <DataItem label="注册资本" value={formatAmount(info.register_capital)} />
        </div>
      </div>

      {(info.total_shares != null || info.circ_shares != null) && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <BarChart3 className="h-4 w-4 text-cyan-600" />股本信息
          </h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <DataItem label="总股本" value={formatShares(info.total_shares)} />
            <DataItem label="流通股本" value={formatShares(info.circ_shares)} />
            <DataItem label="总市值" value={formatMarketCap(info.total_mv)} />
            <DataItem label="流通市值" value={formatMarketCap(info.circ_mv)} />
          </div>
          {hasEm && (
            <p className="mt-2 text-xs text-slate-400">股本数据来源: 东方财富</p>
          )}
        </div>
      )}

      {(info.pe_dynamic != null || info.pe_static != null || info.pb_ratio != null) && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <TrendingUp className="h-4 w-4 text-cyan-600" />估值指标
          </h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <DataItem
              label="市盈率(动)"
              value={info.pe_dynamic != null ? info.pe_dynamic.toFixed(2) : '-'}
            />
            <DataItem
              label="市盈率(静)"
              value={info.pe_static != null ? info.pe_static.toFixed(2) : '-'}
            />
            <DataItem
              label="市净率"
              value={info.pb_ratio != null ? info.pb_ratio.toFixed(2) : '-'}
            />
          </div>
        </div>
      )}

      {info.main_business && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <FileText className="h-4 w-4 text-cyan-600" />主营业务
          </h3>
          <p className="text-sm leading-relaxed text-slate-600">{info.main_business}</p>
        </div>
      )}

      {info.profile && (
        <div className="stock-analysis-panel">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Info className="h-4 w-4 text-cyan-600" />公司简介
          </h3>
          <p className="text-sm leading-relaxed text-slate-600 line-clamp-6">{info.profile}</p>
        </div>
      )}
    </div>
  );
}
