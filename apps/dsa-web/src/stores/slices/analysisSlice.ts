import { analysisApi, DuplicateTaskError } from '../../api/analysis';
import { getParsedApiError } from '../../api/error';
import {
  isObviouslyInvalidStockQuery,
  looksLikeStockCode,
  validateStockCode,
} from '../../utils/validation';
import type { StoreGet, StoreSet, SubmitAnalysisOptions } from '../types';

export interface AnalysisSlice {
  submitAnalysis: (options?: SubmitAnalysisOptions) => Promise<void>;
}

let analyzeRequestSeq = 0;

export function resetAnalysisSeq() {
  analyzeRequestSeq = 0;
}

export function createAnalysisSlice(get: StoreGet, set: StoreSet): AnalysisSlice {
  return {
    submitAnalysis: async (options) => {
      const state = get();
      const rawStockCode = options?.stockCode ?? state.query;
      const stockCodeInput = rawStockCode.trim();
      const stockName = options?.stockName;
      const selectionSource = options?.selectionSource ?? state.selectionSource;
      const originalQuery = (options?.originalQuery ?? state.query).trim();
      const notify = options?.notify ?? state.notify;
      const forceRefresh = options?.forceRefresh ?? false;
      const promptTemplateId = options?.promptTemplateId;

      if (!stockCodeInput) {
        set({ inputError: '请输入股票代码', duplicateError: null });
        return;
      }

      if (selectionSource !== 'autocomplete' && isObviouslyInvalidStockQuery(stockCodeInput)) {
        set({ inputError: '请输入有效的股票代码或股票名称', duplicateError: null });
        return;
      }

      let normalizedStockCode = stockCodeInput;
      if (selectionSource === 'autocomplete' || looksLikeStockCode(stockCodeInput)) {
        const { valid, message, normalized } = validateStockCode(stockCodeInput);
        if (!valid) {
          set({ inputError: message, duplicateError: null });
          return;
        }
        normalizedStockCode = normalized;
      }

      set({
        inputError: undefined,
        duplicateError: null,
        error: null,
        isAnalyzing: true,
      });

      const requestId = ++analyzeRequestSeq;
      try {
        await analysisApi.analyzeAsync({
          stockCode: normalizedStockCode,
          reportType: 'detailed',
          stockName,
          originalQuery: originalQuery || stockCodeInput,
          selectionSource,
          notify,
          forceRefresh,
          promptTemplateId,
        });

        if (requestId !== analyzeRequestSeq) {
          return;
        }

        set({
          query: '',
          selectionSource: 'manual',
          pendingAutoSelectCode: normalizedStockCode,
        });
      } catch (error) {
        if (requestId !== analyzeRequestSeq) {
          return;
        }

        if (error instanceof DuplicateTaskError) {
          set({
            duplicateError: `股票 ${error.stockCode} 正在分析中，请等待完成`,
          });
          return;
        }

        set({ error: getParsedApiError(error) });
      } finally {
        if (requestId === analyzeRequestSeq) {
          set({ isAnalyzing: false });
        }
      }
    },
  };
}
