import { useMemo } from 'react';
import { useMessage } from '@assistant-ui/react';
import type { AgentExecutionTrace } from '../../api/agent';
import { isRecord } from './AgentReasoningUtils';
import { buildTeamBoardModel, type TeamBoardModel } from './TeamBoardUtils';

export type TeamBoardState = {
  active: boolean;
  model: TeamBoardModel;
};

export const useTeamBoardState = (): TeamBoardState => {
  const messageStatus = useMessage((state) => state.status?.type);
  const active = messageStatus === 'running' || messageStatus === 'requires-action';
  const content = useMessage((state) => state.content);
  const stageData = useMessage((state) => state.metadata?.unstable_data);
  const rawTrace = useMessage((state) => (
    state.metadata?.custom?.agent_execution_trace
      ?? state.metadata?.custom?.agentExecutionTrace
  ));
  const trace = isRecord(rawTrace) ? rawTrace as unknown as AgentExecutionTrace : null;
  const model = useMemo(
    () => buildTeamBoardModel(content, stageData, trace, active),
    [content, stageData, trace, active],
  );
  return { active, model };
};
