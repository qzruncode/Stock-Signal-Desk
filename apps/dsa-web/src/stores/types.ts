import type { ParsedApiError } from '../api/error';
import type { AnalysisReport, HistoryItem, HistoryListResponse, TaskInfo } from '../types/analysis';

export type SelectionSource = 'manual' | 'autocomplete' | 'import';

export type FetchHistoryOptions = {
  autoSelectFirst?: boolean;
  reset?: boolean;
  silent?: boolean;
};

export type SubmitAnalysisOptions = {
  stockCode?: string;
  stockName?: string;
  originalQuery?: string;
  selectionSource?: SelectionSource;
  notify?: boolean;
  forceRefresh?: boolean;
  promptTemplateId?: string;
};

export interface DashboardState {
  query: string;
  selectionSource: SelectionSource;
  notify: boolean;
  inputError?: string;
  duplicateError: string | null;
  error: ParsedApiError | null;
  isAnalyzing: boolean;
  historyItems: HistoryItem[];
  selectedHistoryIds: number[];
  isDeletingHistory: boolean;
  isLoadingHistory: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  currentPage: number;
  selectedReport: AnalysisReport | null;
  isLoadingReport: boolean;
  activeTasks: TaskInfo[];
  pendingAutoSelectCode: string | null;
  markdownDrawerOpen: boolean;
}

export interface DashboardActions {
  setQuery: (query: string) => void;
  clearError: () => void;
  clearInlineMessages: () => void;
  openMarkdownDrawer: () => void;
  closeMarkdownDrawer: () => void;
  loadInitialHistory: () => Promise<void>;
  refreshHistory: (silent?: boolean) => Promise<void>;
  loadMoreHistory: () => Promise<void>;
  selectHistoryItem: (recordId: number) => Promise<void>;
  toggleHistorySelection: (recordId: number) => void;
  toggleSelectAllVisible: () => void;
  deleteSelectedHistory: () => Promise<void>;
  submitAnalysis: (options?: SubmitAnalysisOptions) => Promise<void>;
  setNotify: (notify: boolean) => void;
  syncTaskCreated: (task: TaskInfo) => void;
  syncTaskUpdated: (task: TaskInfo) => void;
  syncTaskFailed: (task: TaskInfo) => void;
  removeTask: (taskId: string) => void;
  setPendingAutoSelect: (stockCode: string | null) => void;
  autoSelectByStockCode: (stockCode: string) => Promise<void>;
  resetDashboardState: () => void;
}

export type DashboardStore = DashboardState & DashboardActions;

export type StoreSet = (partial: Partial<DashboardStore>) => void;
export type StoreGet = () => DashboardStore;

export type { HistoryListResponse };
