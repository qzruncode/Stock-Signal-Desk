export type TraceRecord = Record<string, unknown>;

export const isRecord = (value: unknown): value is TraceRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

export const recordsFrom = (value: unknown): TraceRecord[] => (
  Array.isArray(value) ? value.filter(isRecord) : []
);

export const recordValue = (record: TraceRecord | undefined, ...keys: string[]): unknown => {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
};

export const text = (value: unknown, limit = 2_400): string => {
  if (typeof value === 'string') return value.slice(0, limit);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
};

const id = (value: unknown): string => typeof value === 'string' ? value : '';

export const actionId = (record: TraceRecord | undefined): string => id(
  recordValue(record, 'action_id', 'actionId', 'id'),
);

export const toolName = (record: TraceRecord | undefined): string => text(
  recordValue(record, 'tool_name', 'toolName'),
  120,
);

export const errorCode = (record: TraceRecord | undefined): string => text(
  recordValue(record, 'error_code', 'errorCode'),
  120,
);

/** A failed contract attempt that is expected to be followed by a retry. */
