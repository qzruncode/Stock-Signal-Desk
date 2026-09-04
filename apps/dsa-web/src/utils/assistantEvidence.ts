export type AssistantEvidenceItem = {
  title: string;
  source?: string;
  publishedAt?: string;
  summary?: string;
  url?: string;
  attributes?: Array<{ name: string; value: string }>;
};

export type AssistantEvidenceReference = {
  evidenceId: string;
  actionId?: string;
  toolCallId?: string;
  toolName?: string;
  dataTime?: string;
  observedAt?: string;
  sourceLabels: string[];
  sourceRefs: string[];
  resultSummary?: string;
  resultItems: AssistantEvidenceItem[];
  success?: boolean;
  partial?: boolean;
  warnings: string[];
  errors: string[];
};

const EVIDENCE_MARKER = /【\s*(?:(?:证据)\s*[:：]?\s*)?(ev_[^\s】]+)\s*】/gu;
const EVIDENCE_HREF_PREFIX = '#assistant-evidence-';
const CIRCLED_DIGITS = [
  '①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
  '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳',
];

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const readValue = (record: Record<string, unknown>, ...keys: string[]): unknown => {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key];
  }
  return undefined;
};

const textValue = (value: unknown, limit = 1_600): string => {
  if (typeof value !== 'string' && typeof value !== 'number') return '';
  const text = String(value).trim();
  return text.slice(0, limit);
};

const stringList = (value: unknown, limit = 1_600): string[] => {
  const values = Array.isArray(value) ? value : typeof value === 'string' ? [value] : [];
  return [...new Set(values.map((item) => textValue(item, limit)).filter(Boolean))];
};

const recordList = (value: unknown): Record<string, unknown>[] => (
  Array.isArray(value) ? value.filter(isRecord) : []
);

const emptyReference = (evidenceId: string): AssistantEvidenceReference => ({
  evidenceId,
  sourceLabels: [],
  sourceRefs: [],
  resultItems: [],
  warnings: [],
  errors: [],
});

const appendUnique = (target: string[], values: string[]) => {
  values.forEach((value) => {
    if (!target.includes(value)) target.push(value);
  });
};

const resultItemFrom = (value: unknown): AssistantEvidenceItem | null => {
  if (!isRecord(value)) return null;
  const title = textValue(readValue(value, 'title', 'name', 'label'), 360);
  const source = textValue(readValue(value, 'source', 'source_name', 'sourceName'), 320);
  const publishedAt = textValue(readValue(value, 'published_at', 'publishedAt', 'date', 'time'), 160);
  const summary = textValue(readValue(value, 'summary', 'excerpt', 'description'), 700);
  const url = textValue(readValue(value, 'url', 'link'), 1_000);
  const attributes = recordList(readValue(value, 'attributes'))
    .map((attribute) => ({
      name: textValue(readValue(attribute, 'name', 'key', 'label'), 80),
      value: textValue(readValue(attribute, 'value'), 160),
    }))
    .filter((attribute) => attribute.name && attribute.value)
    .slice(0, 5);
  if (!title && !source && !publishedAt && !summary && !url && attributes.length === 0) return null;
  return {
    title: title || source || '资料摘录',
    ...(source ? { source } : {}),
    ...(publishedAt ? { publishedAt } : {}),
    ...(summary ? { summary } : {}),
    ...(url ? { url } : {}),
    ...(attributes.length > 0 ? { attributes } : {}),
  };
};

const resultItemsFrom = (value: unknown): AssistantEvidenceItem[] => (
  recordList(value)
    .map(resultItemFrom)
    .filter((item): item is AssistantEvidenceItem => item !== null)
    .slice(0, 12)
);

const appendResultItems = (target: AssistantEvidenceItem[], items: AssistantEvidenceItem[]) => {
  items.forEach((item) => {
    const key = `${item.title}\u0000${item.url ?? ''}\u0000${item.summary ?? ''}`;
    const existingIndex = target.findIndex((candidate) => (
      `${candidate.title}\u0000${candidate.url ?? ''}\u0000${candidate.summary ?? ''}` === key
    ));
    if (existingIndex === -1 && target.length < 12) {
      target.push(item);
    } else if (existingIndex >= 0 && !target[existingIndex].attributes && item.attributes) {
      target[existingIndex] = { ...target[existingIndex], attributes: item.attributes };
    }
  });
};

const applyRecord = (target: AssistantEvidenceReference, record: Record<string, unknown>) => {
  const actionId = textValue(readValue(record, 'action_id', 'actionId'), 160);
  const toolCallId = textValue(readValue(record, 'tool_call_id', 'toolCallId'), 160);
  const toolName = textValue(readValue(record, 'tool_name', 'toolName'), 160);
  const dataTime = textValue(readValue(record, 'data_time', 'dataTime'), 160);
  const observedAt = textValue(readValue(record, 'observed_at', 'observedAt'), 160);
  const resultSummary = textValue(readValue(record, 'result_summary', 'resultSummary'), 900);
  const success = readValue(record, 'success');
  const partial = readValue(record, 'partial');

  if (!target.actionId && actionId) target.actionId = actionId;
  if (!target.toolCallId && toolCallId) target.toolCallId = toolCallId;
  if (!target.toolName && toolName) target.toolName = toolName;
  if (!target.dataTime && dataTime) target.dataTime = dataTime;
  if (!target.observedAt && observedAt) target.observedAt = observedAt;
  if (!target.resultSummary && resultSummary) target.resultSummary = resultSummary;
  if (typeof success === 'boolean' && target.success === undefined) target.success = success;
  if (partial === true) target.partial = true;

  appendUnique(target.sourceLabels, stringList(readValue(record, 'source_labels', 'sourceLabels'), 320));
  appendUnique(target.sourceRefs, stringList(readValue(record, 'source_refs', 'sourceRefs'), 1_000));
  appendUnique(target.warnings, stringList(readValue(record, 'warnings'), 900));
  appendUnique(target.errors, stringList(readValue(record, 'errors', 'error'), 1_200));
  appendResultItems(target.resultItems, resultItemsFrom(readValue(record, 'result_items', 'resultItems')));
};

const evidenceIdFrom = (record: Record<string, unknown>, includeGenericId = true): string => (
  textValue(readValue(
    record,
    ...(includeGenericId ? ['evidence_id', 'evidenceId', 'id'] : ['evidence_id', 'evidenceId']),
  ), 160)
);

const matchingResult = (
  record: Record<string, unknown>,
  byAction: Map<string, Record<string, unknown>>,
  byToolCall: Map<string, Record<string, unknown>>,
): Record<string, unknown> | undefined => {
  const actionId = textValue(readValue(record, 'action_id', 'actionId'), 160);
  const toolCallId = textValue(readValue(record, 'tool_call_id', 'toolCallId'), 160);
  return (actionId ? byAction.get(actionId) : undefined)
    ?? (toolCallId ? byToolCall.get(toolCallId) : undefined);
};

const upsert = (
  index: Map<string, AssistantEvidenceReference>,
  evidenceId: string,
  ...records: Array<Record<string, unknown> | undefined>
) => {
  const reference = index.get(evidenceId) ?? emptyReference(evidenceId);
  records.forEach((record) => {
    if (record) applyRecord(reference, record);
  });
  index.set(evidenceId, reference);
};

/**
 * Builds the small, user-facing evidence projection from the durable trace.
 * The answer only needs this bounded projection; raw tool payloads stay out of
 * the chat DOM and remain available in the run inspection surface.
 */
export const assistantEvidenceIndexFromTrace = (
  trace: unknown,
): Map<string, AssistantEvidenceReference> => {
  if (!isRecord(trace)) return new Map();

  const toolResults = recordList(readValue(trace, 'tool_results', 'toolResults'));
  const byAction = new Map<string, Record<string, unknown>>();
  const byToolCall = new Map<string, Record<string, unknown>>();
  toolResults.forEach((result) => {
    const actionId = textValue(readValue(result, 'action_id', 'actionId'), 160);
    const toolCallId = textValue(readValue(result, 'tool_call_id', 'toolCallId'), 160);
    if (actionId) byAction.set(actionId, result);
    if (toolCallId) byToolCall.set(toolCallId, result);
  });

  const index = new Map<string, AssistantEvidenceReference>();
  recordList(readValue(trace, 'evidence')).forEach((evidence) => {
    const evidenceId = evidenceIdFrom(evidence);
    if (!evidenceId) return;
    upsert(index, evidenceId, evidence, matchingResult(evidence, byAction, byToolCall));
  });

  // Some older traces put the evidence ID on the projected tool result. Keep
  // those records usable without requiring a backend migration first.
  toolResults.forEach((result) => {
    const evidenceId = evidenceIdFrom(result, false);
    if (evidenceId) upsert(index, evidenceId, result);
  });

  recordList(readValue(trace, 'claim_evidence', 'claimEvidence')).forEach((claim) => {
    recordList(readValue(claim, 'evidence')).forEach((evidence) => {
      const evidenceId = evidenceIdFrom(evidence);
      if (evidenceId) upsert(index, evidenceId, evidence, matchingResult(evidence, byAction, byToolCall));
    });
    stringList(readValue(claim, 'evidence_ids', 'evidenceIds'), 160).forEach((evidenceId) => {
      if (index.has(evidenceId)) return;
      // Claim validation may retain the model's shortened ID while the
      // durable evidence projection carries the canonical ID. Reuse the
      // canonical reference for an unambiguous prefix instead of creating an
      // empty placeholder that would shadow the detailed record on hover.
      const prefixMatches = [...new Set(index.values())].filter((reference) => (
        reference.evidenceId !== evidenceId
        && reference.evidenceId.startsWith(evidenceId)
      ));
      if (prefixMatches.length === 1) {
        index.set(evidenceId, prefixMatches[0]);
      } else {
        upsert(index, evidenceId);
      }
    });
  });

  return index;
};

export const assistantEvidenceFootnoteLabel = (index: number): string => (
  CIRCLED_DIGITS[index] ?? String(index + 1)
);

/** Resolves a model citation to an exact ID or to one unambiguous ID prefix. */
export const assistantEvidenceReferenceForId = (
  index: Map<string, AssistantEvidenceReference>,
  evidenceId: string,
): AssistantEvidenceReference | undefined => {
  const exact = index.get(evidenceId);
  if (exact) return exact;
  if (!evidenceId.startsWith('ev_')) return undefined;
  const matches = [...index.values()].filter((reference) => (
    reference.evidenceId.startsWith(evidenceId)
  ));
  return matches.length === 1 ? matches[0] : undefined;
};

/** Converts the model's durable evidence markers into compact Markdown links. */
export const replaceAssistantEvidenceMarkers = (text: string): string => {
  const ordinalById = new Map<string, number>();
  let nextOrdinal = 0;
  return text.replace(EVIDENCE_MARKER, (marker, rawEvidenceId: string) => {
    const evidenceId = rawEvidenceId.trim();
    if (!evidenceId) return marker;
    let ordinal = ordinalById.get(evidenceId);
    if (ordinal === undefined) {
      ordinal = nextOrdinal;
      nextOrdinal += 1;
      ordinalById.set(evidenceId, ordinal);
    }
    const label = assistantEvidenceFootnoteLabel(ordinal);
    return `[${label}](${EVIDENCE_HREF_PREFIX}${encodeURIComponent(evidenceId)})`;
  });
};

export const assistantEvidenceIdFromHref = (href?: string): string | null => {
  if (!href?.startsWith(EVIDENCE_HREF_PREFIX)) return null;
  const encodedId = href.slice(EVIDENCE_HREF_PREFIX.length);
  if (!encodedId) return null;
  try {
    const evidenceId = decodeURIComponent(encodedId).trim();
    return evidenceId || null;
  } catch {
    return null;
  }
};
