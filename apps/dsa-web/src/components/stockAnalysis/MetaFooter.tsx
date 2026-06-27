import React from 'react';
import { Clock } from 'lucide-react';
import { formatSourceChain } from '../../utils/stockAnalysisFormat';

export const MetaFooter: React.FC<{
  fetchedAt?: string;
  cached?: boolean;
  source?: string;
  sourceChain?: string[];
}> = ({ fetchedAt, cached, source, sourceChain }) => (
  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
    <Clock className="h-3 w-3" />
    <span>
      数据获取时间: {fetchedAt ? new Date(fetchedAt).toLocaleString('zh-CN') : '-'}
      {cached ? ' · 缓存' : ' · 实时'}
    </span>
    {(source || sourceChain) && (
      <>
        <span className="text-slate-300">|</span>
        <span>数据源: {source ?? formatSourceChain(sourceChain)}</span>
      </>
    )}
  </div>
);