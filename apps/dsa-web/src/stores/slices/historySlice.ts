import { getParsedApiError } from '../../api/error';
import { historyApi } from '../../api/history';
import { getRecentStartDate, getTodayInShanghai } from '../../utils/format';
import type { FetchHistoryOptions, HistoryListResponse, StoreGet, StoreSet } from '../types';

const PAGE_SIZE = 20;

export interface HistorySlice {
  loadInitialHistory: () => Promise<void>;
  refreshHistory: (silent?: boolean) => Promise<void>;
  loadMoreHistory: () => Promise<void>;
  selectHistoryItem: (recordId: number) => Promise<void>;
  toggleHistorySelection: (recordId: number) => void;
  toggleSelectAllVisible: () => void;
  deleteSelectedHistory: () => Promise<void>;
}

let historyRequestSeq = 0;
let reportRequestSeq = 0;

export function resetHistorySeqs() {
  historyRequestSeq += 1;
  reportRequestSeq = 0;
}

function buildHistoryParams(page: number) {
  return {
    startDate: getRecentStartDate(30),
    endDate: getTodayInShanghai(),
    page,
    limit: PAGE_SIZE,
  };
}

async function fetchHistory(
  get: StoreGet,
  set: StoreSet,
  options: FetchHistoryOptions = {},
): Promise<HistoryListResponse | null> {
  const { autoSelectFirst = false, reset = true, silent = false } = options;
  const currentState = get();
  const page = reset ? 1 : currentState.currentPage + 1;
  const requestId = ++historyRequestSeq;

  if (!silent) {
    set(
      reset
        ? { isLoadingHistory: true, isLoadingMore: false, currentPage: 1 }
        : { isLoadingMore: true },
    );
  }

  try {
    const response = await historyApi.getList(buildHistoryParams(page));
    if (requestId !== historyRequestSeq) {
      return null;
    }

    if (silent && reset) {
      const existingIds = new Set(get().historyItems.map((item) => item.id));
      const newItems = response.items.filter((item) => !existingIds.has(item.id));
      if (newItems.length > 0) {
        set({ historyItems: [...newItems, ...get().historyItems] });
      }
    } else if (reset) {
      set({
        historyItems: response.items,
        currentPage: 1,
      });
    } else {
      set({
        historyItems: [...get().historyItems, ...response.items],
        currentPage: page,
      });
    }

    if (!silent) {
      const totalLoaded = reset ? response.items.length : get().historyItems.length;
      set({ hasMore: totalLoaded < response.total });
    }

    const visibleIds = new Set(get().historyItems.map((item) => item.id));
    set({
      selectedHistoryIds: get().selectedHistoryIds.filter((id) => visibleIds.has(id)),
    });

    if (autoSelectFirst && response.items.length > 0 && !get().selectedReport) {
      await get().selectHistoryItem(response.items[0].id);
    }

    return response;
  } catch (error) {
    if (requestId !== historyRequestSeq) {
      return null;
    }
    set({ error: getParsedApiError(error) });
    return null;
  } finally {
    if (requestId === historyRequestSeq) {
      set({
        isLoadingHistory: false,
        isLoadingMore: false,
      });
    }
  }
}

export function createHistorySlice(get: StoreGet, set: StoreSet): HistorySlice {
  return {
    loadInitialHistory: async () => {
      await fetchHistory(get, set, { autoSelectFirst: true, reset: true });
    },

    refreshHistory: async (silent = false) => {
      await fetchHistory(get, set, { reset: true, silent });
    },

    loadMoreHistory: async () => {
      const state = get();
      if (state.isLoadingMore || !state.hasMore) {
        return;
      }
      await fetchHistory(get, set, { reset: false });
    },

    selectHistoryItem: async (recordId) => {
      const requestId = ++reportRequestSeq;
      const shouldShowInitialLoading = !get().selectedReport;

      if (shouldShowInitialLoading) {
        set({ isLoadingReport: true });
      }

      try {
        const report = await historyApi.getDetail(recordId);
        if (requestId !== reportRequestSeq) {
          return;
        }

        set({
          selectedReport: report,
          error: null,
          isLoadingReport: false,
        });
      } catch (error) {
        if (requestId !== reportRequestSeq) {
          return;
        }

        set({
          error: getParsedApiError(error),
          isLoadingReport: false,
        });
      }
    },

    toggleHistorySelection: (recordId) => {
      const selected = new Set(get().selectedHistoryIds);
      if (selected.has(recordId)) {
        selected.delete(recordId);
      } else {
        selected.add(recordId);
      }

      set({ selectedHistoryIds: Array.from(selected) });
    },

    toggleSelectAllVisible: () => {
      const visibleIds = get().historyItems.map((item) => item.id);
      const selectedIds = get().selectedHistoryIds;
      const visibleSet = new Set(visibleIds);
      const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));

      set({
        selectedHistoryIds: allSelected
          ? selectedIds.filter((id) => !visibleSet.has(id))
          : Array.from(new Set([...selectedIds, ...visibleIds])),
      });
    },

    deleteSelectedHistory: async () => {
      const state = get();
      const recordIds = Array.from(new Set(state.selectedHistoryIds));
      if (recordIds.length === 0 || state.isDeletingHistory) {
        return;
      }

      set({ isDeletingHistory: true });
      try {
        await historyApi.deleteRecords(recordIds);

        const deletedIds = new Set(recordIds);
        const selectedWasDeleted = state.selectedReport?.meta.id !== undefined
          && deletedIds.has(state.selectedReport.meta.id);

        set({ selectedHistoryIds: [] });

        const freshPage = await fetchHistory(get, set, { reset: true });

        if (selectedWasDeleted) {
          const nextItem = freshPage?.items?.[0];
          if (nextItem) {
            await get().selectHistoryItem(nextItem.id);
          } else {
            set({ selectedReport: null });
          }
        }
      } catch (error) {
        set({ error: getParsedApiError(error) });
      } finally {
        set({ isDeletingHistory: false });
      }
    },
  };
}
