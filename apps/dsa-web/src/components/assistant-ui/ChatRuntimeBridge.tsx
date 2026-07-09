import type React from 'react';
import { useEffect, useRef } from 'react';
import { useThreadRuntime } from '@assistant-ui/react';
import type { ExportedMessageRepository } from '@assistant-ui/core';
import { type ChatConversationDetail } from '../../api/agent';

/**
 * ChatRuntimeBridge:把后端会话详情(ChatConversationDetail)桥接到 assistant-ui
 * 的 ThreadRuntime。负责 hydration(import/reset)、续流(startRun)与生成态判定。
 *
 * 从 ChatHomePage 抽出,使页面文件守住 600 行预算;本文件只导出该组件,
 * 辅助函数不导出以保持 Fast Refresh 干净。
 */

const PENDING_ASSISTANT_SUFFIX = '-assistant-pending';

const normalizeMessageRole = (role: string): 'user' | 'assistant' | 'system' => {
  if (role === 'user' || role === 'assistant' || role === 'system') {
    return role;
  }
  return 'assistant';
};

const toRuntimeMessages = (messages: ChatConversationDetail['messages']) =>
  messages
    .filter((message) => (message.content || '').trim().length > 0)
    .map((message) => ({
      id: message.id,
      role: normalizeMessageRole(message.role),
      createdAt: message.createdAt ? new Date(message.createdAt) : new Date(),
      content: [{ type: 'text' as const, text: message.content || '' }],
    }));

const getConversationHydrationKey = (detail: ChatConversationDetail): string => {
  const lastMessage = detail.messages.at(-1);
  return [
    detail.id,
    detail.updatedAt,
    detail.isGenerating ? 'running' : 'idle',
    detail.resumeState?.active ? 'resumable' : 'not-resumable',
    detail.resumeState?.status ?? '',
    detail.resumeState?.afterChunkIndex ?? '',
    detail.threadState?.headId ?? '',
    detail.messages.length,
    lastMessage?.id ?? '',
    lastMessage?.content ?? '',
  ].join('|');
};

const hasToolParts = (threadState: ChatConversationDetail['threadState']): boolean => {
  if (!threadState?.messages?.length) return false;
  return threadState.messages.some((entry) => {
    const content = entry.message?.content;
    return Array.isArray(content)
      && content.some((part) => {
        if (!part || typeof part !== 'object') return false;
        const partType = (part as Record<string, unknown>).type;
        return partType === 'tool-call' || partType === 'tool-result';
      });
  });
};

const removeTrailingAssistant = (
  messages: ChatConversationDetail['messages'],
): ChatConversationDetail['messages'] => {
  const lastMessage = messages.at(-1);
  if (lastMessage?.role !== 'assistant') {
    return messages;
  }
  return messages.slice(0, -1);
};

const getLastAssistantText = (messages: ChatConversationDetail['messages']): string => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === 'assistant') {
      return (message.content || '').trim();
    }
  }
  return '';
};

export type ChatRuntimeBridgeProps = {
  conversationDetail: ChatConversationDetail | null;
  onThreadRuntime: (threadRuntime: ReturnType<typeof useThreadRuntime>) => void;
  onPrepareResumeExisting: (conversationId: string, afterChunkIndex: number | null) => void;
};

export const ChatRuntimeBridge: React.FC<ChatRuntimeBridgeProps> = ({
  conversationDetail,
  onThreadRuntime,
  onPrepareResumeExisting,
}) => {
  const threadRuntime = useThreadRuntime();
  const appliedHydrationKeyRef = useRef<string | null>(null);

  useEffect(() => {
    onThreadRuntime(threadRuntime);
  }, [onThreadRuntime, threadRuntime]);

  useEffect(() => {
    if (!conversationDetail) {
      appliedHydrationKeyRef.current = null;
      onPrepareResumeExisting('', null);
      threadRuntime.cancelRun();
      threadRuntime.reset([]);
      return;
    }

    const hydrationKey = getConversationHydrationKey(conversationDetail);
    if (appliedHydrationKeyRef.current === hydrationKey) {
      return;
    }
    appliedHydrationKeyRef.current = hydrationKey;

    onPrepareResumeExisting(conversationDetail.id, null);
    threadRuntime.cancelRun();
    threadRuntime.reset([]);

    const isGenerating = conversationDetail.isGenerating === true;
    const canReplayStream = conversationDetail.resumeState?.active === true;
    const threadStateHasToolParts = hasToolParts(conversationDetail.threadState);
    const retainedFinalText = (conversationDetail.resumeState?.assistantText || '').trim();
    const lastAssistantText = getLastAssistantText(conversationDetail.messages);
    const retainedTextMismatch = Boolean(
      retainedFinalText && lastAssistantText && retainedFinalText !== lastAssistantText,
    );
    const shouldReplayStream = isGenerating
      || (canReplayStream
        && (
          (conversationDetail.resumeState?.hasToolEvents === true && !threadStateHasToolParts)
          || retainedTextMismatch
        ));
    const pendingId = `${conversationDetail.id}${PENDING_ASSISTANT_SUFFIX}`;

    // isGenerating 时:threadState 是上次完成时的旧快照(不含本次 user 消息),
    // 而 messages 是生成开始时刚落的完整历史(含本次 user)。故续流场景一律用
    // messages,确保恢复完整历史;非生成态才用 threadState(保留分支结构)。
    const messagesWithoutPending = conversationDetail.messages.filter((m) => m.id !== pendingId);
    const visibleMessages = shouldReplayStream
      ? removeTrailingAssistant(messagesWithoutPending)
      : conversationDetail.messages;

    if (!shouldReplayStream && conversationDetail.threadState?.messages?.length) {
      threadRuntime.import(
        conversationDetail.threadState as unknown as ExportedMessageRepository,
      );
    } else {
      threadRuntime.reset(toRuntimeMessages(visibleMessages));
    }

    if (!shouldReplayStream) {
      const lastMessage = conversationDetail.messages.at(-1);
      if (lastMessage?.role === 'user') {
        onPrepareResumeExisting(conversationDetail.id, null);
        threadRuntime.startRun({
          parentId: lastMessage.id,
          sourceId: lastMessage.id,
          runConfig: {},
        });
      }
      return;
    }

    // 后端仍在生成或保留了刚完成的 run:通过 /agent/chat + resume_existing
    // 复用 useDataStreamRuntime 的完整 data-stream 管道,保证 K 线图等 tool UI
    // 与初次生成一致。
    const parentId = visibleMessages.at(-1)?.id ?? null;
    onPrepareResumeExisting(conversationDetail.id, 0);
    threadRuntime.startRun({
      parentId,
      sourceId: parentId,
      runConfig: {},
    });
  }, [conversationDetail, threadRuntime, onPrepareResumeExisting]);

  return null;
};
