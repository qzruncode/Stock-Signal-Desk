import { createContext, useContext } from 'react';

/** 一条待审批的工具调用(后端 approval-request data 事件)。 */
export interface PendingApproval {
  tool_call_id: string;
  tool_name: string;
  symbol: string;
  reason: string;
}

/**
 * 审批上下文:approval-request 经 data-stream 协议到达后,累进当前 assistant
 * 消息的 metadata.unstable_data。AssistantMessage 组件用 useMessage 读取并调
 * registerApproval 写入,内联工具 UI(BuyCriteriaToolUI 等)据此渲染确认/取消
 * 按钮,通过 approveToolCall 调 POST /agent/approve。
 *
 * 注意:useDataStreamRuntime 的 onData 回调只在 protocol:'ui-message-stream' 时
 * 生效,本项目用 'data-stream',onData 是死代码。故拦截改在消息层(读 unstable_data),
 * 而非 runtime 层。该拦截天然覆盖首连与续流两条路径(两者最终都把 chunk 累积进
 * 同一条 assistant 消息的 unstable_data)。
 *
 * pendingApprovals: tool_call_id -> 审批信息
 * registerApproval: 消息层拦截到 approval-request 后写入
 * approveToolCall: 用户决定后调用(发请求到后端)
 */
export interface ApprovalContextValue {
  pendingApprovals: Record<string, PendingApproval>;
  registerApproval: (approval: PendingApproval) => void;
  approveToolCall: (toolCallId: string, approved: boolean) => void;
}

export const ApprovalContext = createContext<ApprovalContextValue>({
  pendingApprovals: {},
  registerApproval: () => {},
  approveToolCall: () => {},
});

export const useApproval = (toolCallId: string): PendingApproval | undefined => {
  const { pendingApprovals } = useContext(ApprovalContext);
  return pendingApprovals[toolCallId];
};
