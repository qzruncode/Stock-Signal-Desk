import apiClient from '../api/index';
import { loadJsonFromStorage } from './storage';

export interface WatchlistGroup {
  id: string;
  name: string;
  codes: string[];
  source?: string;
  sortOrder?: number;
}

export const WATCHLIST_GROUPS_UPDATED_EVENT = 'dsa-watchlist-groups-updated';

// Legacy localStorage key (pre-DB). Kept only for one-time migration.
const LEGACY_STORAGE_KEY = 'dsa.watchlist.groups.v1';
const MIGRATED_FLAG_KEY = 'dsa.watchlist.groups.migrated.v1';

function notifyGroupsUpdated() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(WATCHLIST_GROUPS_UPDATED_EVENT));
}

export async function fetchWatchlistGroups(): Promise<WatchlistGroup[]> {
  const response = await apiClient.get<{ groups: WatchlistGroup[] }>('/api/v1/watchlist/groups');
  return response.data.groups || [];
}

/** Create a group, or replace an existing group's codes when the name matches. */
export async function upsertWatchlistGroup(
  name: string,
  codes: string[],
  source = 'manual',
): Promise<WatchlistGroup> {
  const response = await apiClient.post<WatchlistGroup>('/api/v1/watchlist/groups', {
    name,
    codes,
    source,
  });
  notifyGroupsUpdated();
  return response.data;
}

/** Update an existing group's name and/or codes by id. */
export async function updateWatchlistGroup(
  id: string,
  patch: { name?: string; codes?: string[] },
): Promise<WatchlistGroup> {
  const response = await apiClient.patch<WatchlistGroup>(
    `/api/v1/watchlist/groups/${id}`,
    patch,
  );
  notifyGroupsUpdated();
  return response.data;
}

export async function deleteWatchlistGroup(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/watchlist/groups/${id}`);
  notifyGroupsUpdated();
}

function normalizeLegacyGroups(value: unknown): { name: string; codes: string[] }[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((group): group is { name: string; codes: string[] } => (
      Boolean(group)
      && typeof group.name === 'string'
      && Array.isArray(group.codes)
    ))
    .map((group) => ({ name: group.name, codes: group.codes.map((c) => String(c)) }));
}

/**
 * One-time migration: import any legacy localStorage groups into the database.
 * Idempotent — guarded by a sentinel flag and backend name-based upsert.
 */
export async function migrateLegacyWatchlistGroups(): Promise<void> {
  if (typeof window === 'undefined') return;
  if (window.localStorage.getItem(MIGRATED_FLAG_KEY)) return;

  const legacy = normalizeLegacyGroups(loadJsonFromStorage<unknown>(LEGACY_STORAGE_KEY, []));
  try {
    for (const group of legacy) {
      const name = group.name.trim();
      if (!name) continue;
      await upsertWatchlistGroup(name, group.codes, 'manual');
    }
    // Mark done and drop the legacy payload only after a clean import.
    window.localStorage.setItem(MIGRATED_FLAG_KEY, '1');
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // Leave the legacy data + flag untouched so the next load retries.
  }
}
