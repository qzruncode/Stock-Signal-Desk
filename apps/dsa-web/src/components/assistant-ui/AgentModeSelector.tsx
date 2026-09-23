import type { FC } from 'react';
import { CompactSelect } from '../common';
import {
  AGENT_MODE_OPTIONS,
  agentModeLabel,
  type AgentProductMode,
} from '../../utils/agentMode';

export type AgentModeSelectorProps = {
  value: AgentProductMode;
  onChange: (value: AgentProductMode) => void;
  disabled?: boolean;
};

/** The five product modes are selected before a turn and stay fixed during it. */
export const AgentModeSelector: FC<AgentModeSelectorProps> = ({
  value,
  onChange,
  disabled = false,
}) => (
  <div className="flex items-center" title={`${agentModeLabel(value)}：本轮固定执行路径`}>
    <CompactSelect
      id="agent-product-mode"
      value={value}
      options={AGENT_MODE_OPTIONS}
      onChange={(nextValue) => onChange(nextValue as AgentProductMode)}
      ariaLabel="选择 Agent 工作模式"
      disabled={disabled}
      className="w-fit"
      triggerClassName="w-auto border-0 bg-transparent px-1 shadow-none hover:border-transparent hover:bg-muted/60 focus:ring-0 focus:ring-offset-0 data-[state=open]:border-transparent data-[state=open]:bg-muted/70"
    />
  </div>
);

export default AgentModeSelector;
