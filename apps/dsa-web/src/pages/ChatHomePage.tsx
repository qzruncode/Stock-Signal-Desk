import { ChatHomeRuntimeSurface } from '../components/assistant-ui/ChatHomeRuntimeSurface';
import { ConfirmDialog } from '../components/common';
import { useChatController } from '../hooks/useChatController';

const ChatHomePage = () => {
  const {
    pendingBatchDeletion, runtime, selectedConversationDetail, prepareResumeExisting,
    shouldDetachTerminalStream, threadRuntimeRef, streamError, setStreamError,
    conversations, selectedConversationId, isLoadingConversations, isConversationSwitching,
    conversationSwitchError, handleRetryConversation, handleCreateConversation, handleSelectConversation,
    handleRenameConversation, handleDeleteConversation, handleBatchDeleteConversations, handleDeleteUserTurn,
    handleUserCancelRun, isClearingConversations, isDeletingConversations, handleClearAllConversations,
    approvalDecision, approvalError, handleInterruptDecision, pendingConversationDeletion,
    confirmDeleteConversation, setPendingConversationDeletion, confirmBatchDeleteConversations, setPendingBatchDeletion,
    isConfirmingClearAll, confirmClearAllConversations, setIsConfirmingClearAll,
  } = useChatController();
  const batchDeletionMessage = pendingBatchDeletion
    ? [
      `确定删除 ${pendingBatchDeletion.ids.length} 个选中的对话吗？`,
      ...pendingBatchDeletion.titles.slice(0, 5),
      ...(pendingBatchDeletion.titles.length > 5 ? ['...'] : []),
    ].join('\n')
    : '';
  return (
    <>
      <ChatHomeRuntimeSurface
        runtime={runtime}
        bridgeProps={{
          conversationDetail: selectedConversationDetail,
          onPrepareResumeExisting: prepareResumeExisting,
          shouldDetachTerminalStream,
          onThreadRuntime: (threadRuntime) => {
            threadRuntimeRef.current = threadRuntime;
          },
        }}
        layoutProps={{
          streamError,
          onDismissError: () => setStreamError(null),
          conversations,
          selectedConversationId,
          isLoadingConversations,
          isConversationSwitching,
          conversationSwitchError,
          onRetryConversation: handleRetryConversation,
          onCreateConversation: handleCreateConversation,
          onSelectConversation: handleSelectConversation,
          onRenameConversation: handleRenameConversation,
          onDeleteConversation: handleDeleteConversation,
          onBatchDeleteConversations: handleBatchDeleteConversations,
          onDeleteUserTurn: handleDeleteUserTurn,
          onCancelRun: handleUserCancelRun,
          isClearingConversations: isClearingConversations || isDeletingConversations,
          onClearAllConversations: handleClearAllConversations,
          pendingInterrupt: selectedConversationDetail?.pendingInterrupt ?? null,
          approvalDecision,
          approvalError,
          onInterruptDecision: handleInterruptDecision,
        }}
      />
      <ConfirmDialog
        isOpen={pendingConversationDeletion !== null}
        title="删除对话"
        message={`确定删除对话“${pendingConversationDeletion?.title || '新对话'}”吗？`}
        confirmText="删除"
        cancelText="取消"
        isDanger
        onConfirm={confirmDeleteConversation}
        onCancel={() => setPendingConversationDeletion(null)}
      />
      <ConfirmDialog
        isOpen={pendingBatchDeletion !== null}
        title="删除选中的对话"
        message={batchDeletionMessage}
        confirmText="删除"
        cancelText="取消"
        isDanger
        onConfirm={confirmBatchDeleteConversations}
        onCancel={() => setPendingBatchDeletion(null)}
      />
      <ConfirmDialog
        isOpen={isConfirmingClearAll}
        title="清除全部会话历史"
        message={`确定清除全部 ${conversations.length} 个会话及其历史记录吗？\n此操作不可恢复。`}
        confirmText="清除全部"
        cancelText="取消"
        isDanger
        onConfirm={confirmClearAllConversations}
        onCancel={() => setIsConfirmingClearAll(false)}
      />
    </>
  );
};
export default ChatHomePage;
