import { useCallback, useState, type Dispatch, type SetStateAction, type RefObject } from 'react';
import type { ThreadRuntime } from '@assistant-ui/react';
import { useQueryClient } from '@tanstack/react-query';
import { agentApi, type ChatConversationDetail, type ChatConversationItem } from '../api/agent';
import { toApiErrorMessage } from '../api/error';
import { conversationKey } from '../utils/conversationQueries';
import type { ActiveStream, ConversationLoadState } from '../utils/chatHomeConstants';

type PendingBatchDeletion = { ids: string[]; titles: string[] };
type Options = {
  conversations: ChatConversationItem[];
  selectedConversationId: string | null;
  selectedConversationIdRef: RefObject<string | null>;
  setSelectedConversationId: Dispatch<SetStateAction<string | null>>;
  setSelectedConversationDetail: Dispatch<SetStateAction<ChatConversationDetail | null>>;
  setConversationLoadState: Dispatch<SetStateAction<ConversationLoadState>>;
  setStreamError: Dispatch<SetStateAction<string | null>>;
  activeStreamRef: RefObject<ActiveStream | null>;
  resumeExistingRef: RefObject<{ conversationId: string; afterChunkIndex: number } | null>;
  threadRuntimeRef: RefObject<ThreadRuntime | null>;
  beginConversationSelection: (id: string) => boolean;
  createConversation: () => Promise<ChatConversationItem>;
  loadConversationDetail: (id: string) => Promise<ChatConversationDetail>;
  refreshConversations: () => Promise<ChatConversationItem[]>;
};

export function useConversationActions({
    conversations, selectedConversationId, selectedConversationIdRef, setSelectedConversationId,
    setSelectedConversationDetail, setConversationLoadState, setStreamError, activeStreamRef,
    resumeExistingRef, threadRuntimeRef, beginConversationSelection, createConversation,
    loadConversationDetail, refreshConversations,
}: Options) {
  const queryClient = useQueryClient();
  const [isClearingConversations, setIsClearingConversations] = useState(false);
  const [isDeletingConversations, setIsDeletingConversations] = useState(false);
  const [pendingConversationDeletion, setPendingConversationDeletion] = useState<ChatConversationItem | null>(null);
  const [pendingBatchDeletion, setPendingBatchDeletion] = useState<PendingBatchDeletion | null>(null);
  const [isConfirmingClearAll, setIsConfirmingClearAll] = useState(false);
  const handleRenameConversation = useCallback((conversation: ChatConversationItem) => {
    const nextTitle = window.prompt('输入新的对话名称', conversation.title || '新对话');
    if (!nextTitle || nextTitle.trim() === conversation.title) {
      return;
    }
    void (async () => {
      try {
        await agentApi.renameConversation(conversation.id, nextTitle.trim());
        await refreshConversations();
        if (conversation.id === selectedConversationId) {
          await loadConversationDetail(conversation.id);
        }
      } catch (error) {
        setStreamError(toApiErrorMessage(error, '会话重命名失败，请稍后重试'));
      }
    })();
  }, [loadConversationDetail, refreshConversations, selectedConversationId, setStreamError]);

  const handleDeleteConversation = useCallback((conversation: ChatConversationItem) => {
    if (isClearingConversations || isDeletingConversations) {
      return;
    }
    setPendingConversationDeletion(conversation);
  }, [isClearingConversations, isDeletingConversations]);

  const confirmDeleteConversation = useCallback(() => {
    const conversation = pendingConversationDeletion;
    if (!conversation || isClearingConversations || isDeletingConversations) {
      return;
    }
    setPendingConversationDeletion(null);
    setIsDeletingConversations(true);
    void (async () => {
      try {
        await agentApi.deleteConversation(conversation.id);
        queryClient.removeQueries({ queryKey: conversationKey(conversation.id), exact: true });
        const items = await refreshConversations();
        if (conversation.id !== selectedConversationId) {
          return;
        }
        if (items.length === 0) {
          await createConversation();
          return;
        }
        if (items[0]?.id) {
          beginConversationSelection(items[0].id);
        }
      } catch (error) {
        setStreamError(toApiErrorMessage(error, '删除会话失败，请稍后重试'));
      } finally {
        setIsDeletingConversations(false);
      }
    })();
  }, [beginConversationSelection, createConversation, isClearingConversations, isDeletingConversations, pendingConversationDeletion, refreshConversations, selectedConversationId, queryClient, setStreamError]);

  const handleBatchDeleteConversations = useCallback((conversationIds: string[]) => {
    if (isClearingConversations || isDeletingConversations || conversationIds.length === 0) {
      return;
    }
    const ids = Array.from(new Set(conversationIds));
    const titles = conversations
      .filter((conversation) => ids.includes(conversation.id))
      .map((conversation) => conversation.title || '新对话');
    setPendingBatchDeletion({ ids, titles });
  }, [conversations, isClearingConversations, isDeletingConversations]);

  const confirmBatchDeleteConversations = useCallback(() => {
    const pendingDeletion = pendingBatchDeletion;
    if (!pendingDeletion || isClearingConversations || isDeletingConversations) {
      return;
    }
    setPendingBatchDeletion(null);
    setIsDeletingConversations(true);

    const currentConversationId = selectedConversationIdRef.current;
    const isDeletingCurrentConversation = Boolean(
      currentConversationId && pendingDeletion.ids.includes(currentConversationId),
    );
    if (isDeletingCurrentConversation) {
      activeStreamRef.current = null;
      resumeExistingRef.current = null;
      threadRuntimeRef.current?.cancelRun();
    }

    void (async () => {
      try {
        await Promise.all(pendingDeletion.ids.map((conversationId) => agentApi.deleteConversation(conversationId)));
        pendingDeletion.ids.forEach((conversationId) => {
          queryClient.removeQueries({ queryKey: conversationKey(conversationId), exact: true });
        });
        const items = await refreshConversations();
        if (!isDeletingCurrentConversation) {
          return;
        }
        if (items.length === 0) {
          await createConversation();
          return;
        }
        setSelectedConversationDetail(null);
        setConversationLoadState(null);
        if (items[0]?.id) {
          beginConversationSelection(items[0].id);
        }
      } catch (error) {
        setStreamError(toApiErrorMessage(error, '批量删除会话失败，请稍后重试'));
        void refreshConversations().catch(() => undefined);
      } finally {
        setIsDeletingConversations(false);
      }
    })();
  }, [beginConversationSelection, createConversation, isClearingConversations, isDeletingConversations, pendingBatchDeletion, refreshConversations, activeStreamRef, queryClient, resumeExistingRef, selectedConversationIdRef, setConversationLoadState, setSelectedConversationDetail, setStreamError, threadRuntimeRef]);

  const handleClearAllConversations = useCallback(() => {
    if (isClearingConversations || isDeletingConversations || conversations.length === 0) {
      return;
    }
    setIsConfirmingClearAll(true);
  }, [conversations.length, isClearingConversations, isDeletingConversations]);

  const confirmClearAllConversations = useCallback(() => {
    if (!isConfirmingClearAll || isClearingConversations || isDeletingConversations) {
      return;
    }
    setIsConfirmingClearAll(false);
    setIsClearingConversations(true);
    activeStreamRef.current = null;
    resumeExistingRef.current = null;
    threadRuntimeRef.current?.cancelRun();
    void (async () => {
      try {
        await agentApi.clearAllConversations();
        queryClient.removeQueries({ queryKey: ['conversation'] });
        selectedConversationIdRef.current = null;
        setSelectedConversationId(null);
        setSelectedConversationDetail(null);
        setConversationLoadState(null);
        setStreamError(null);
        const items = await refreshConversations();
        if (items.length === 0) {
          await createConversation();
          return;
        }
        if (items[0]?.id) {
          beginConversationSelection(items[0].id);
        }
      } catch (error) {
        setStreamError(toApiErrorMessage(error, '清除全部会话历史失败，请稍后重试'));
      } finally {
        setIsClearingConversations(false);
      }
    })();
  }, [beginConversationSelection, createConversation, isClearingConversations, isConfirmingClearAll, isDeletingConversations, refreshConversations, activeStreamRef, queryClient, resumeExistingRef, selectedConversationIdRef, setConversationLoadState, setSelectedConversationDetail, setSelectedConversationId, setStreamError, threadRuntimeRef]);

  return {
    isClearingConversations, isDeletingConversations, pendingConversationDeletion, pendingBatchDeletion,
    isConfirmingClearAll, setPendingConversationDeletion, setPendingBatchDeletion, setIsConfirmingClearAll,
    handleRenameConversation, handleDeleteConversation, confirmDeleteConversation, handleBatchDeleteConversations,
    confirmBatchDeleteConversations, handleClearAllConversations, confirmClearAllConversations,
  };
}
