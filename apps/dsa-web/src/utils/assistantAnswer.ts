export type AssistantContentPart = {
  readonly type: string;
  readonly text?: string;
  readonly name?: string;
  readonly providerMetadata?: unknown;
};

export type AssistantDisplayKind = 'progress' | 'answer';

/** Read the server-owned display boundary without inferring it from copy. */
export const assistantDisplayKindOf = (
  part: Pick<AssistantContentPart, 'providerMetadata'>,
): AssistantDisplayKind | null => {
  const metadata = part.providerMetadata;
  if (typeof metadata !== 'object' || metadata === null || Array.isArray(metadata)) return null;
  const dsa = (metadata as Record<string, unknown>).dsa;
  if (typeof dsa !== 'object' || dsa === null || Array.isArray(dsa)) return null;
  const displayKind = (dsa as Record<string, unknown>).displayKind
    ?? (dsa as Record<string, unknown>).display_kind;
  return displayKind === 'progress' || displayKind === 'answer' ? displayKind : null;
};

export const hasAssistantDisplayMetadata = (
  part: Pick<AssistantContentPart, 'providerMetadata'>,
): boolean => assistantDisplayKindOf(part) !== null;

export const assistantPublishedAnswerTextFromContent = (
  content: readonly AssistantContentPart[],
): string => {
  // ``agent-answer-boundary`` is emitted immediately before the accepted
  // answer. Select the latest boundary instead of concatenating every
  // answer-looking text part; repair attempts and terminal hydration can
  // otherwise make an older full answer reappear before the final one.
  const latestBoundary = content.reduce(
    (index, part, currentIndex) => (
      part.type === 'data' && part.name === 'agent-answer-boundary' ? currentIndex : index
    ),
    -1,
  );
  const scoped = latestBoundary >= 0 ? content.slice(latestBoundary + 1) : content;
  const typed = scoped
    .filter((part) => part.type === 'text' && assistantDisplayKindOf(part) === 'answer')
    .map((part) => part.text || '')
    .filter(Boolean);
  if (typed.length > 0) return typed.join('\n\n');
  if (latestBoundary >= 0) {
    return scoped
      .filter((part) => part.type === 'text' && !assistantDisplayKindOf(part))
      .map((part) => part.text || '')
      .filter(Boolean)
      .join('');
  }
  return '';
};

/**
 * A terminal run can contain a substantive no-tool candidate before the
 * publication fragment (for example after evidence repair).  The ordered
 * stream identifies that body as the first progress text after the final
 * tool call; keep it in the conclusion area instead of hiding it with the
 * execution disclosure.
 */
export const assistantPostToolBodyText = (
  content: readonly AssistantContentPart[],
): string => {
  let lastToolPart = -1;
  content.forEach((part, index) => {
    if (part.type === 'tool-call') lastToolPart = index;
  });
  if (lastToolPart < 0) return '';
  return content
    .slice(lastToolPart + 1)
    .find((part) => (
      part.type === 'text'
      && assistantDisplayKindOf(part) === 'progress'
      && Boolean(part.text?.trim())
    ))?.text || '';
};

export const assistantAnswerTextFromContent = (
  content: readonly AssistantContentPart[],
): string => {
  const publishedAnswer = assistantPublishedAnswerTextFromContent(content);
  // `displayKind: answer` is the server-owned publication boundary.  A
  // terminal trace can also contain a substantive progress fragment before
  // that boundary (for example the model's pre-repair draft), but combining
  // both fragments duplicates the answer and can surface citations that only
  // existed in the discarded draft.  Keep that fragment in the execution
  // disclosure; the published part is the only conclusion rendered here.
  if (publishedAnswer) return publishedAnswer;

  const postToolBody = assistantPostToolBodyText(content);
  if (postToolBody) return postToolBody;

  let lastToolPart = -1;
  content.forEach((part, index) => {
    if (part.type === 'tool-call') lastToolPart = index;
  });
  return content
    .slice(lastToolPart + 1)
    .map((part) => (part.type === 'text' ? part.text || '' : ''))
    .filter(Boolean)
    .join('\n\n');
};
