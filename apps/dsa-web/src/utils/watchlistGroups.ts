import { loadJsonFromStorage, saveJsonToStorage } from './storage';

export interface WatchlistGroup {
  id: string;
  name: string;
  codes: string[];
}

export const WATCHLIST_GROUPS_STORAGE_KEY = 'dsa.watchlist.groups.v1';
export const WATCHLIST_GROUPS_UPDATED_EVENT = 'dsa-watchlist-groups-updated';

function normalizeGroups(value: unknown): WatchlistGroup[] {
  return Array.isArray(value)
    ? value.filter((group): group is WatchlistGroup => (
      Boolean(group)
      && typeof group.id === 'string'
      && typeof group.name === 'string'
      && Array.isArray(group.codes)
    ))
    : [];
}

export function loadWatchlistGroups(): WatchlistGroup[] {
  return normalizeGroups(loadJsonFromStorage<unknown>(WATCHLIST_GROUPS_STORAGE_KEY, []));
}

export function saveWatchlistGroups(groups: WatchlistGroup[]) {
  if (typeof window === 'undefined') return;
  saveJsonToStorage(WATCHLIST_GROUPS_STORAGE_KEY, groups);
  window.dispatchEvent(new Event(WATCHLIST_GROUPS_UPDATED_EVENT));
}

export function upsertWatchlistGroup(name: string, codes: string[]): WatchlistGroup {
  const cleanCodes = Array.from(new Set(codes.map((code) => code.trim()).filter(Boolean)));
  const groups = loadWatchlistGroups();
  const existingIndex = groups.findIndex((group) => group.name === name);
  const nextGroup: WatchlistGroup = existingIndex >= 0
    ? { ...groups[existingIndex], codes: cleanCodes }
    : { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, name, codes: cleanCodes };
  const nextGroups = existingIndex >= 0
    ? groups.map((group, index) => (index === existingIndex ? nextGroup : group))
    : [...groups, nextGroup];
  saveWatchlistGroups(nextGroups);
  return nextGroup;
}
