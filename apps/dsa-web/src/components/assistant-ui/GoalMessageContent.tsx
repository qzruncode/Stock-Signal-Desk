import type { FC } from 'react';
import { useMessage } from '@assistant-ui/react';
import { assistantPublishedAnswerTextFromContent } from '../../utils/assistantAnswer';
import { AssistantTypingIndicator } from './AssistantTypingIndicator';
import { NativeAssistantParts } from './ThreadNativeParts';
import { StructuredAnswerReferences } from './StructuredAnswerReferences';
import { structuredAnswerFromTrace } from './StructuredAnswerReferencesUtils';

/** Model prose and native tools share one ordered chat stream, live or replayed. */
export const GoalMessageContent: FC<{ animateAcceptedAnswer?: boolean }> = ({ animateAcceptedAnswer }) => {
  const running = useMessage((state) => state.status?.type === 'running');
  const answerText = useMessage((state) => assistantPublishedAnswerTextFromContent(state.content));
  const trace = useMessage((state) => (
    state.metadata?.custom?.agent_execution_trace ?? state.metadata?.custom?.agentExecutionTrace
  ));
  const hasChart = useMessage((state) => state.content.some((part) => (
    part.type === 'data' && part.name === 'stock-chart'
  )));
  const hasTools = useMessage((state) => state.content.some((part) => part.type === 'tool-call'));

  return (
    <>
      <NativeAssistantParts animateAcceptedAnswer={animateAcceptedAnswer} includeStageParts={false} />
      <StructuredAnswerReferences
        answer={structuredAnswerFromTrace(trace)}
        renderedText={answerText}
        renderCharts={!hasChart}
        renderActions={!hasTools}
      />
      {running && !answerText.trim() ? <AssistantTypingIndicator /> : null}
    </>
  );
};
