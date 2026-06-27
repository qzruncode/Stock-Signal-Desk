import React from 'react';
import { MetaFooter } from './MetaFooter';

interface MetaCarrier {
  _fetched_at?: string;
  _cached?: boolean;
  source?: string;
  source_chain?: string[];
}

function Spinner({ size = 'lg', label }: { size?: 'sm' | 'lg'; label: string }) {
  const isSmall = size === 'sm';
  return (
    <div className={isSmall ? 'flex h-20 items-center justify-center' : 'flex h-40 items-center justify-center'}>
      <div className={isSmall ? 'flex flex-col items-center gap-2' : 'flex flex-col items-center gap-3'}>
        <div className={isSmall
          ? 'h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan'
          : 'h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan'} />
        <span className={isSmall ? 'text-xs text-slate-400' : 'text-sm text-slate-400'}>{label}</span>
      </div>
    </div>
  );
}

function EmptyBlock({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/50 p-8 text-center">
      <p className="text-sm text-slate-400">{text}</p>
    </div>
  );
}

export function DataSection<T extends MetaCarrier>({
  loading,
  loadingLabel,
  data,
  emptyText,
  render,
}: {
  loading: boolean;
  loadingLabel: string;
  data: T | null | undefined;
  emptyText: string;
  render: (data: T) => React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      {loading ? (
        <Spinner label={loadingLabel} />
      ) : data ? (
        <>
          {render(data)}
          <MetaFooter
            fetchedAt={data._fetched_at}
            cached={data._cached}
            source={data.source}
            sourceChain={data.source_chain}
          />
        </>
      ) : (
        <EmptyBlock text={emptyText} />
      )}
    </div>
  );
}