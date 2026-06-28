import type { StoreSet } from '../types';

export interface DrawerSlice {
  openMarkdownDrawer: () => void;
  closeMarkdownDrawer: () => void;
  setPendingAutoSelect: (stockCode: string | null) => void;
}

export function createDrawerSlice(set: StoreSet): DrawerSlice {
  return {
    openMarkdownDrawer: () => set({ markdownDrawerOpen: true }),
    closeMarkdownDrawer: () => set({ markdownDrawerOpen: false }),
    setPendingAutoSelect: (stockCode) => set({ pendingAutoSelectCode: stockCode }),
  };
}
