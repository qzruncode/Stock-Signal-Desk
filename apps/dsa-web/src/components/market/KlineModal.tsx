import React from 'react';
import { X } from 'lucide-react';
import KLineChartPanel from '../KLineChartPanel';
import type { KlineResponse } from '../../api/kline';

interface KlineModalProps {
  stock: { code: string; name: string };
  data: KlineResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

export const KlineModal: React.FC<KlineModalProps> = ({ stock, data, loading, error, onClose }) => (
  <div
    className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-8"
    onClick={onClose}
  >
    <div
      className="flex w-full max-w-3xl flex-col rounded-2xl bg-white shadow-xl"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            {stock.name}
            <span className="ml-2 font-mono text-sm font-normal text-slate-500">{stock.code}</span>
          </h2>
          <p className="text-xs text-slate-400">日线 · 前复权 · 近 250 个交易日</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
          aria-label="关闭"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden px-4 py-4">
        <KLineChartPanel
          data={data}
          loading={loading}
          error={error}
        />
      </div>
    </div>
  </div>
);