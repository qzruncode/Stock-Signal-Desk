import React from 'react';
import { FileText } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { BatchResultItem } from '../../api/batch';

interface DetailViewProps {
  result: BatchResultItem | null;
}

export const DetailView: React.FC<DetailViewProps> = ({ result }) => {
  if (!result) return null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-subtle px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <FileText className="h-4 w-4 text-muted-text" />
          <h2 className="font-mono text-lg font-semibold text-foreground">{result.code}</h2>
          <span className={cn(
            'rounded-full px-2 py-0.5 text-xs',
            result.success ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600',
          )}
          >
            {result.success ? '分析成功' : '分析失败'}
          </span>
          <span className="text-xs text-muted-text">{result.model}</span>
        </div>
        <p className="mt-2 text-sm text-muted-text">{result.summary}</p>
      </div>
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-5 py-4 text-sm leading-7 text-secondary-text">
        {result.text || '无输出'}
      </pre>
    </div>
  );
};