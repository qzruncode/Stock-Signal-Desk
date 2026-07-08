import { createContext, useContext } from 'react';

/** 一条待审批的工具调用(后端 approval-request data 事件)。 */
export interface PendingApproval {
  tool_call_id: string;
  tool_name: string;
  symbol: string;
  reason: string;
}

/**
 * 审批上下文:ChatHomePage 的 onData 回调把后端 approval-request 写入,
 * BuyCriteriaToolUI 等内联工具 UI 据此渲染确认/取消按钮,并通过
 * approveToolCall 调 POST /agent/approve。
 *
 * pendingApprovals: tool_call_id -> 审批信息
 * approveToolCall: 用户决定后调用(发请求到后端)
 */
export interface ApprovalContextValue {
  pendingApprovals: Record<string, PendingApproval>;
  approveToolCall: (toolCallId: string, approved: boolean) => void;
}

export const ApprovalContext = createContext<ApprovalContextValue>({
  pendingApprovals: {},
  approveToolCall: () => {},
});

export const useApproval = (toolCallId: string): PendingApproval | undefined => {
  const { pendingApprovals } = useContext(ApprovalContext);
  return pendingApprovals[toolCallId];
};
