import type { StoreSet } from '../types';

export interface QuerySlice {
  setQuery: (query: string) => void;
  clearError: () => void;
  clearInlineMessages: () => void;
  setNotify: (notify: boolean) => void;
}

export function createQuerySlice(set: StoreSet): QuerySlice {
  return {
    setQuery: (query) => {
      set({
        query,
        selectionSource: 'manual',
        inputError: undefined,
        duplicateError: null,
      });
    },
    clearError: () => set({ error: null }),
    clearInlineMessages: () => set({ inputError: undefined, duplicateError: null }),
    setNotify: (notify) => set({ notify }),
  };
}
