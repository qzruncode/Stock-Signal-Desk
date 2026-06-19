// apps/dsa-web/src/components/buyCriteria/BuyCriteriaPanel.tsx
import { useEffect } from 'react';
import { useBuyCriteria } from '../../hooks/useBuyCriteria';
import { CRITERIA_ORDER } from '../../api/buyCriteria';
import { SummaryBar } from './SummaryBar';
import { CriterionCard } from './CriterionCard';

interface BuyCriteriaPanelProps {
  symbol: string;
}

export function BuyCriteriaPanel({ symbol }: BuyCriteriaPanelProps) {
  const { state, startAnalysis, stopAnalysis } = useBuyCriteria();

  // Reset when symbol changes
  useEffect(() => {
    stopAnalysis();
  }, [symbol, stopAnalysis]);

  if (!symbol) {
    return (
      <div className="rounded-2xl border-2 border-dashed border-slate-200 p-8 text-center text-slate-400">
        请先选择一只股票
      </div>
    );
  }

  const criteriaStatuses = Object.fromEntries(
    Object.entries(state.criteria).map(([id, cs]) => [id, cs.status]),
  );

  return (
    <div className="space-y-4">
      <SummaryBar
        symbol={symbol}
        stockName={symbol}
        criteriaStatuses={criteriaStatuses}
        finalDecision={state.finalDecision}
        summary={state.analysisSummary}
        isRunning={state.isRunning}
        onStart={() => startAnalysis(symbol)}
        onRestart={() => startAnalysis(symbol)}
      />

      {state.error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {state.error}
        </div>
      )}

      <div className="space-y-3">
        {CRITERIA_ORDER.map((c, idx) => {
          const cs = state.criteria[c.id];
          return (
            <CriterionCard
              key={c.id}
              criterionId={c.id}
              criterionName={c.name}
              index={idx}
              status={cs.status}
              result={cs.result}
            />
          );
        })}
      </div>
    </div>
  );
}
