import { beforeEach, describe, expect, it } from 'vitest';
import { loadJsonFromStorage, saveJsonToStorage } from '../storage';

describe('storage helpers', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('loads validated JSON from localStorage', () => {
    saveJsonToStorage('settings', { enabled: true });

    expect(
      loadJsonFromStorage(
        'settings',
        { enabled: false },
        (value): value is { enabled: boolean } => value !== null && typeof value === 'object' && 'enabled' in value,
      ),
    ).toEqual({ enabled: true });
  });

  it('returns fallback for invalid JSON and failed validation', () => {
    window.localStorage.setItem('bad-json', '{');
    window.localStorage.setItem('bad-shape', JSON.stringify({ enabled: true }));

    expect(loadJsonFromStorage('bad-json', 42)).toBe(42);
    expect(loadJsonFromStorage('bad-shape', [], Array.isArray)).toEqual([]);
  });
});
