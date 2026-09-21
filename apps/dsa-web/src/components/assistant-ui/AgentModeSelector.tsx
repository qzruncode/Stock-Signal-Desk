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

/** The four product modes are selected before a turn and stay fixed during it. */
export const AgentModeSelector: FC<AgentModeSelectorProps> = ({
  value,
  onChange,
  disabled = false,
}) => (
  <div className="flex items-center gap-1.5" title={`${agentModeLabel(value)}：本轮固定执行路径`}>
    <span className="hidden text-[10px] text-muted-foreground sm:inline">工作模式</span>
    <CompactSelect
      id="agent-product-mode"
      value={value}
      options={AGENT_MODE_OPTIONS}
      onChange={(nextValue) => onChange(nextValue as AgentProductMode)}
      ariaLabel="选择 Agent 工作模式"
      disabled={disabled}
      className="w-[116px] sm:w-[132px]"
    />
  </div>
);

export default AgentModeSelector;
