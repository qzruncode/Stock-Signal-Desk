export type AgentProductMode = 'auto' | 'direct' | 'plan' | 'team';

export const DEFAULT_AGENT_MODE: AgentProductMode = 'auto';
export const AGENT_MODE_STORAGE_KEY = 'dsa.agent-product-mode.v2';

export const AGENT_MODE_OPTIONS: Array<{
  value: AgentProductMode;
  label: string;
}> = [
  { value: 'auto', label: 'Auto · 自动' },
  { value: 'direct', label: 'Direct · 直接' },
  { value: 'plan', label: 'Plan · 计划' },
  { value: 'team', label: 'Team · 协作' },
];

export function isAgentProductMode(value: unknown): value is AgentProductMode {
  return value === 'auto' || value === 'direct' || value === 'plan' || value === 'team';
}

export function readStoredAgentMode(): AgentProductMode {
  if (typeof window === 'undefined') return DEFAULT_AGENT_MODE;
  try {
    const stored = window.localStorage.getItem(AGENT_MODE_STORAGE_KEY);
    return isAgentProductMode(stored) ? stored : DEFAULT_AGENT_MODE;
  } catch {
    return DEFAULT_AGENT_MODE;
  }
}

export function persistAgentMode(mode: AgentProductMode): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(AGENT_MODE_STORAGE_KEY, mode);
  } catch {
    // Storage can be unavailable in private browsing; in-memory state still works.
  }
}

export function agentModeLabel(mode: unknown): string {
  if (mode === 'auto') return 'Auto · 自动';
  if (mode === 'plan' || mode === 'planned') return 'Plan · 计划';
  if (mode === 'team' || mode === 'multi_agent_team') return 'Team · 协作';
  return 'Direct · 直接';
}
