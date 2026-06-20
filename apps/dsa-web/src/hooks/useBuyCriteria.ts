// apps/dsa-web/src/hooks/useBuyCriteria.ts
import { useCallback, useEffect, useRef, useState } from 'react';
import { CRITERIA_ORDER, buyCriteriaApi } from '../api/buyCriteria';
import type {
  AnalysisCompleteEvent,
  CriterionId,
  CriterionResult,
  CriterionStatus,
} from '../api/buyCriteria';

// ── Types ────────────────────────────────────────────────────────────────

interface CriterionState {
  status: CriterionStatus;
  result: CriterionResult | null;
}

interface AnalysisState {
  criteria: Record<CriterionId, CriterionState>;
  finalDecision: '可买入' | '不可买入' | null;
  analysisSummary: string;
  isRunning: boolean;
  isCached: boolean;
  cachedAt: string | null;
  error: string | null;
}

interface UseBuyCriteriaReturn {
  state: AnalysisState;
  startAnalysis: (symbol: string, preFetchedData?: Record<string, unknown>) => void;
  stopAnalysis: () => void;
  loadCached: (symbol: string) => Promise<void>;
}

// ── Helpers ──────────────────────────────────────────────────────────────

function createInitialState(): AnalysisState {
  const criteria = {} as Record<CriterionId, CriterionState>;
  for (const c of CRITERIA_ORDER) {
    criteria[c.id] = { status: 'idle', result: null };
  }
  return { criteria, finalDecision: null, analysisSummary: '', isRunning: false, isCached: false, cachedAt: null, error: null };
}

// ── Hook ─────────────────────────────────────────────────────────────────

export function useBuyCriteria(): UseBuyCriteriaReturn {
  const [state, setState] = useState<AnalysisState>(createInitialState);
  const eventSourceRef = useRef<EventSource | null>(null);

  const stopAnalysis = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setState(prev => ({ ...prev, isRunning: false }));
  }, []);

  const loadCached = useCallback(async (symbol: string) => {
    try {
      const cached = await buyCriteriaApi.getCached(symbol);
      const criteria = {} as Record<CriterionId, CriterionState>;

      // Initialize all as idle first
      for (const c of CRITERIA_ORDER) {
        criteria[c.id] = { status: 'idle', result: null };
      }

      // Fill in from cached results
      for (const r of cached.results) {
        criteria[r.criterion_id as CriterionId] = {
          status: r.passed ? 'pass' : 'fail',
          result: r,
        };
      }

      // Mark remaining idle criteria as not_evaluated (early termination)
      if (cached.failed_count > 0 && cached.stopped_at) {
        const stoppedIdx = CRITERIA_ORDER.findIndex(c => c.id === cached.stopped_at);
        for (let i = stoppedIdx + 1; i < CRITERIA_ORDER.length; i++) {
          criteria[CRITERIA_ORDER[i].id] = { status: 'not_evaluated', result: null };
        }
      }

      setState({
        criteria,
        finalDecision: cached.final_decision,
        analysisSummary: cached.summary,
        isRunning: false,
        isCached: true,
        cachedAt: cached.created_at,
        error: null,
      });
    } catch {
      // No cached record or error — just leave state as-is
    }
  }, []);

  const startAnalysis = useCallback(
    (symbol: string, preFetchedData?: Record<string, unknown>) => {
      // Stop any existing analysis
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }

      // Reset state
      setState({ ...createInitialState(), isRunning: true });

      const url = buyCriteriaApi.getCriteriaStreamUrl(symbol, preFetchedData);
      const es = new EventSource(url, { withCredentials: true });
      eventSourceRef.current = es;

      es.addEventListener('criterion_start', (event: MessageEvent) => {
        const data = JSON.parse(event.data) as { criterion_id: CriterionId };
        setState(prev => ({
          ...prev,
          criteria: {
            ...prev.criteria,
            [data.criterion_id]: { status: 'running', result: null },
          },
        }));
      });

      es.addEventListener('criterion_complete', (event: MessageEvent) => {
        const result = JSON.parse(event.data) as CriterionResult;
        setState(prev => {
          const newCriteria = {
            ...prev.criteria,
            [result.criterion_id]: {
              status: result.passed ? 'pass' : 'fail',
              result,
            },
          };

          // If this criterion failed, mark remaining as not_evaluated
          if (!result.passed) {
            for (const c of CRITERIA_ORDER) {
              if (newCriteria[c.id].status === 'idle') {
                newCriteria[c.id] = { status: 'not_evaluated', result: null };
              }
            }
          }

          return { ...prev, criteria: newCriteria };
        });
      });

      es.addEventListener('analysis_complete', (event: MessageEvent) => {
        const data = JSON.parse(event.data) as AnalysisCompleteEvent;
        setState(prev => ({
          ...prev,
          finalDecision: data.final_decision,
          analysisSummary: data.summary,
          isRunning: false,
          isCached: false,
          cachedAt: null,
        }));
        es.close();
        eventSourceRef.current = null;
      });

      es.addEventListener('error', (event: MessageEvent) => {
        // SSE error event with data = backend error
        if (event.data) {
          try {
            const data = JSON.parse(event.data) as { message: string };
            setState(prev => ({
              ...prev,
              error: data.message || '分析出错',
              isRunning: false,
            }));
          } catch {
            setState(prev => ({
              ...prev,
              error: '连接中断',
              isRunning: false,
            }));
          }
        }
        es.close();
        eventSourceRef.current = null;
      });

      es.onerror = () => {
        setState(prev => {
          if (prev.isRunning) {
            return { ...prev, error: '连接中断', isRunning: false };
          }
          return prev;
        });
        es.close();
        eventSourceRef.current = null;
      };
    }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return { state, startAnalysis, stopAnalysis, loadCached };
}
