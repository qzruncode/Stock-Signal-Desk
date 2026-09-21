import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AGENT_MODE_OPTIONS,
  AGENT_MODE_STORAGE_KEY,
  DEFAULT_AGENT_MODE,
  isAgentProductMode,
  persistAgentMode,
  readStoredAgentMode,
} from '../agentMode';

describe('agent product modes', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('exposes exactly Auto, Direct, Plan, and Team to the composer', () => {
    expect(AGENT_MODE_OPTIONS.map((option) => option.value)).toEqual(['auto', 'direct', 'plan', 'team']);
  });

  it('keeps an invalid stored value from becoming a fourth mode', () => {
    window.localStorage.setItem(AGENT_MODE_STORAGE_KEY, 'invalid');
    expect(readStoredAgentMode()).toBe(DEFAULT_AGENT_MODE);
    expect(isAgentProductMode('invalid')).toBe(false);
  });

  it('persists the selected product mode for the next visit', () => {
    persistAgentMode('team');
    expect(readStoredAgentMode()).toBe('team');
  });
});
