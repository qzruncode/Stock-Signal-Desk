import { lazy, Suspense, useEffect } from 'react';

const Stocks = lazy(() => import('../components/settings/StockListSettingsView'));
const Screening = lazy(() => import('../components/settings/IndicatorScreeningSettingsView'));
const Sources = lazy(() => import('../components/settings/RssSettingsView'));

export default function MarketWorkspacePage({ section }: { section: 'stocks' | 'screening' | 'sources' }) {
  const title = { stocks: '股票与自选分组', screening: '指标选股', sources: '财经来源' }[section];
  useEffect(() => {
    document.title = `${title} - Stock Assistant`;
  }, [title]);
  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col gap-4 py-5">
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="min-h-0 flex-1">
        <Suspense fallback={<p>加载中…</p>}>
          {section === 'stocks' ? <Stocks /> : section === 'screening' ? <Screening /> : <Sources />}
        </Suspense>
      </div>
    </div>
  );
}
