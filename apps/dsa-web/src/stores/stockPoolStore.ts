import { create } from 'zustand';
import { initialState } from './initialState';
import { createAnalysisSlice } from './slices/analysisSlice';
import { createDrawerSlice } from './slices/drawerSlice';
import { createHistorySlice, resetHistorySeqs } from './slices/historySlice';
import { createQuerySlice } from './slices/querySlice';
import { createTaskSlice, resetTaskDismissed } from './slices/taskSlice';
import { resetAnalysisSeq } from './slices/analysisSlice';
import type { DashboardStore, StoreGet, StoreSet } from './types';

export const useStockPoolStore = create<DashboardStore>((set, get) => ({
  ...initialState,
  ...createQuerySlice(set as StoreSet),
  ...createDrawerSlice(set as StoreSet),
  ...createHistorySlice(get as StoreGet, set as StoreSet),
  ...createTaskSlice(get as StoreGet, set as StoreSet),
  ...createAnalysisSlice(get as StoreGet, set as StoreSet),

  resetDashboardState: () => {
    resetHistorySeqs();
    resetAnalysisSeq();
    resetTaskDismissed();
    set({ ...initialState });
  },
}));

export type { DashboardStore, DashboardState, DashboardActions } from './types';
export type {
  SelectionSource,
  SubmitAnalysisOptions,
} from './types';
