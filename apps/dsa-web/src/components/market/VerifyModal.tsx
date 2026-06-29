import React from 'react';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { KlineStatusResponse } from '../../api/stocks';

interface VerifyModalProps {
  open: boolean;
  data: KlineStatusResponse | null;
  loading: boolean;
  onClose: () => void;
}

export const VerifyModal: React.FC<VerifyModalProps> = ({ open, data, loading, onClose }) => {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">数据源验证结果</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
          </div>
        ) : data ? (
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-slate-500">股票总数</span>
              <span className="font-semibold text-slate-900">{data.total_stocks} 只</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">K 线数据</span>
              <span className="font-semibold text-emerald-600">
                {data.stocks_with_kline} 只
                {data.total_stocks > 0
                  ? `（${((data.stocks_with_kline / data.total_stocks) * 100).toFixed(1)}%）`
                  : ''}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">缺失数据</span>
              <span className={cn('font-semibold', data.missing > 0 ? 'text-red-500' : 'text-emerald-600')}>
                {data.missing} 只
              </span>
            </div>
            {data.latest_trading_day && (
              <div className="flex justify-between border-t border-slate-100 pt-3">
                <span className="text-slate-500">最近交易日</span>
                <span className="font-medium text-slate-700">{data.latest_trading_day}</span>
              </div>
            )}
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-red-500">获取验证数据失败</p>
        )}
      </div>
    </div>
  );
};