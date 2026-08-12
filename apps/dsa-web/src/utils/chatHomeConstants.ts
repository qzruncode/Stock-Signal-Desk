export type ConversationLoadState = {
  conversationId: string;
  status: 'loading' | 'error';
  message?: string;
} | null;

export type ActiveStream = {
  conversationId: string;
  resumeExisting: boolean;
  /** The durable snapshot shown when the browser stream was attached. */
  initialRunId: string | null;
  /** A new user turn may start while this still describes the prior run. */
  startedFromTerminalSnapshot: boolean;
};

export const MAX_CACHED_CONVERSATIONS = 12;
export const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'partial',
  'failed',
  'cancelled',
  'blocked',
]);
