const HIGHLIGHT_DURATION_MS = 1600;
const highlightTimeouts = new WeakMap<HTMLElement, number>();

export const getChatQuestionDomId = (messageId: string): string => (
  `chat-question-${encodeURIComponent(messageId)}`
);

export const locateChatQuestion = (
  messageId: string,
  ownerDocument: Document = document,
): boolean => {
  const question = ownerDocument.getElementById(getChatQuestionDomId(messageId));
  if (!question) return false;

  const previousTimeout = highlightTimeouts.get(question);
  const ownerWindow = ownerDocument.defaultView ?? window;
  if (previousTimeout !== undefined) {
    ownerWindow.clearTimeout(previousTimeout);
  }

  question.dataset.chatQuestionLocated = 'true';
  question.scrollIntoView({
    behavior: ownerWindow.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'center',
  });
  question.focus({ preventScroll: true });

  const timeout = ownerWindow.setTimeout(() => {
    delete question.dataset.chatQuestionLocated;
    highlightTimeouts.delete(question);
  }, HIGHLIGHT_DURATION_MS);
  highlightTimeouts.set(question, timeout);
  return true;
};
