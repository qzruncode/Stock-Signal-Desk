import type { DashboardState } from './types';

export const initialState: DashboardState = {
  query: '',
  selectionSource: 'manual',
  notify: true,
  inputError: undefined,
  duplicateError: null,
  error: null,
  isAnalyzing: false,
  historyItems: [],
  selectedHistoryIds: [],
  isDeletingHistory: false,
  isLoadingHistory: false,
  isLoadingMore: false,
  hasMore: true,
  currentPage: 1,
  selectedReport: null,
  isLoadingReport: false,
  activeTasks: [],
  pendingAutoSelectCode: null,
  markdownDrawerOpen: false,
};
