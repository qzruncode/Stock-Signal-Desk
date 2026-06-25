import { type MarketMainlineReportResponse } from '../api/marketThemes';

export interface MarketMainlineTaskPayload {
  phase?: string;
  stream_text?: string;
  report_draft?: Partial<MarketMainlineReportResponse>;
  report?: MarketMainlineReportResponse;
  llm_used?: boolean;
  model_used?: string | null;
  debug_input?: MarketMainlineReportResponse['debug_input'];
}

export function hasReadyReport(report: MarketMainlineReportResponse | null | undefined): boolean {
  return Boolean(report) && report?.report_pending === false;
}

export function readTaskPayload(value: unknown): MarketMainlineTaskPayload {
  if (!value || typeof value !== 'object') {
    return {};
  }
  const payload = value as Record<string, unknown>;
  return {
    phase: (payload.phase as string | undefined) ?? undefined,
    stream_text: (payload.stream_text as string | undefined) ?? (payload.streamText as string | undefined),
    report_draft:
      (payload.report_draft as Partial<MarketMainlineReportResponse> | undefined)
      ?? (payload.reportDraft as Partial<MarketMainlineReportResponse> | undefined),
    report:
      (payload.report as MarketMainlineReportResponse | undefined)
      ?? undefined,
    llm_used: (payload.llm_used as boolean | undefined) ?? (payload.llmUsed as boolean | undefined),
    model_used: (payload.model_used as string | null | undefined) ?? (payload.modelUsed as string | null | undefined),
    debug_input:
      (payload.debug_input as MarketMainlineReportResponse['debug_input'] | undefined)
      ?? (payload.debugInput as MarketMainlineReportResponse['debug_input'] | undefined),
  };
}

export function mergeDraftIntoReport(
  base: MarketMainlineReportResponse | null,
  draft?: Partial<MarketMainlineReportResponse> | null,
  debugInput?: MarketMainlineReportResponse['debug_input'],
): MarketMainlineReportResponse | null {
  if (!base && !draft && !debugInput) return null;
  return {
    id: draft?.id ?? base?.id,
    report_key: draft?.report_key ?? base?.report_key,
    mode: draft?.mode ?? base?.mode,
    created_at: draft?.created_at ?? base?.created_at,
    generated_at: draft?.generated_at ?? base?.generated_at ?? '',
    as_of_date: draft?.as_of_date ?? base?.as_of_date ?? '',
    overview: draft?.overview ?? base?.overview ?? '',
    full_report: draft?.full_report ?? base?.full_report ?? '',
    market_stage: draft?.market_stage ?? base?.market_stage ?? { label: '', description: '' },
    current_mainlines: draft?.current_mainlines ?? base?.current_mainlines ?? [],
    future_mainlines: draft?.future_mainlines ?? base?.future_mainlines ?? [],
    action_summary: draft?.action_summary ?? base?.action_summary ?? [],
    evidence_digest: draft?.evidence_digest ?? base?.evidence_digest ?? { policy: [], industry: [], market: [] },
    source_summary: draft?.source_summary ?? base?.source_summary,
    llm_used: draft?.llm_used ?? base?.llm_used ?? false,
    model_used: draft?.model_used ?? base?.model_used ?? null,
    raw_stream_output: draft?.raw_stream_output ?? base?.raw_stream_output,
    raw_response: draft?.raw_response ?? base?.raw_response,
    report_pending: draft?.report_pending ?? base?.report_pending,
    _cached: draft?._cached ?? base?._cached,
    debug_input: debugInput ?? draft?.debug_input ?? base?.debug_input,
  };
}

export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? '');
  }
}

export function detectStreamKind(text: string): 'json' | 'report' {
  const trimmed = text.trim();
  return trimmed.startsWith('{') || trimmed.startsWith('[') ? 'json' : 'report';
}

export function formatStreamText(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) {
    return '';
  }

  if (detectStreamKind(trimmed) !== 'json') {
    return trimmed;
  }

  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return trimmed;
  }
}

export function hasStructuredContent(report: Partial<MarketMainlineReportResponse> | null | undefined): boolean {
  if (!report) {
    return false;
  }
  return Boolean(
    (report.overview && report.overview.trim())
    || (report.full_report && report.full_report.trim())
    || (report.current_mainlines && report.current_mainlines.length > 0)
    || (report.future_mainlines && report.future_mainlines.length > 0)
    || (report.action_summary && report.action_summary.length > 0),
  );
}

export function mergeTaskDraft(
  previous: Partial<MarketMainlineReportResponse> | null,
  nextDraft?: Partial<MarketMainlineReportResponse> | null,
): Partial<MarketMainlineReportResponse> | null {
  if (!nextDraft) {
    return previous;
  }
  if (!previous) {
    return nextDraft;
  }

  const previousFullReport = previous.full_report || '';
  const nextFullReport = nextDraft.full_report || '';

  return {
    ...previous,
    ...nextDraft,
    full_report: nextFullReport.length >= previousFullReport.length ? nextFullReport : previousFullReport,
  };
}
