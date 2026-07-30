import type React from 'react';
import { useEffect, useRef } from 'react';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import type { ExportedMessageRepository, ThreadMessageLike } from '@assistant-ui/core';
import type { ReadonlyJSONValue } from 'assistant-stream/utils';
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

const toRuntimeMessages = (
  messages: ChatConversationDetail['messages'],
  latestStage?: NonNullable<ChatConversationDetail['resumeState']>['latestStage'],
): ThreadMessageLike[] => {
  const lastAssistantId = [...messages]
    .reverse()
    .find((message) => message.role === 'assistant')?.id;
  const persistedStage = latestStage as ReadonlyJSONValue | undefined;
  return messages
    .filter((message) => (message.content || '').trim().length > 0)
    .map((message) => ({
      id: message.id,
      role: normalizeMessageRole(message.role),
      createdAt: message.createdAt ? new Date(message.createdAt) : new Date(),
      content: [{ type: 'text' as const, text: message.content || '' }],
      ...(persistedStage && message.id === lastAssistantId
        ? { metadata: { unstable_data: [persistedStage] } }
        : {}),
    }));
};

const withPersistedStage = (
  threadState: ChatConversationDetail['threadState'],
  latestStage?: NonNullable<ChatConversationDetail['resumeState']>['latestStage'],
): ChatConversationDetail['threadState'] => {
  if (!threadState || !latestStage) return threadState;
  let injected = false;
  const messages = [...threadState.messages]
    .reverse()
    .map((entry) => {
      if (injected || entry.message?.role !== 'assistant') return entry;
      injected = true;
      const metadata = (
        typeof entry.message.metadata === 'object'
        && entry.message.metadata !== null
        && !Array.isArray(entry.message.metadata)
      )
        ? entry.message.metadata as Record<string, unknown>
        : {};
      const existing = Array.isArray(metadata.unstable_data)
        ? metadata.unstable_data
        : [];
      return {
        ...entry,
        message: {
          ...entry.message,
          metadata: {
            ...metadata,
            unstable_data: [...existing, latestStage],
          },
        },
      };
    })
    .reverse();
  return {
    ...threadState,
    messages,
  };
};

const getConversationHydrationKey = (detail: ChatConversationDetail): string => {
  const lastMessage = detail.messages.at(-1);
  return [
    detail.id,
    detail.updatedAt,
    detail.isGenerating ? 'running' : 'idle',
    detail.resumeState?.active ? 'resumable' : 'not-resumable',
    detail.resumeState?.status ?? '',
    detail.resumeState?.latestStage?.stage ?? '',
    detail.resumeState?.latestStage?.status ?? '',
    detail.resumeState?.latestStage?.occurredAt
      ?? detail.resumeState?.latestStage?.occurred_at
      ?? '',
    detail.resumeState?.afterChunkIndex ?? '',
    detail.threadState?.headId ?? '',
    detail.messages.length,
    lastMessage?.id ?? '',
    lastMessage?.content ?? '',
  ].join('|');
};

const hasRichParts = (threadState: ChatConversationDetail['threadState']): boolean => {
  if (!threadState?.messages?.length) return false;
  return threadState.messages.some((entry) => {
    const content = entry.message?.content;
    return Array.isArray(content)
      && content.some((part) => {
        if (!part || typeof part !== 'object') return false;
        const partType = (part as Record<string, unknown>).type;
        return partType === 'tool-call' || partType === 'tool-result' || partType === 'reasoning';
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
  const isThreadRunning = useThread((state) => state.isRunning);
  const appliedHydrationKeyRef = useRef<string | null>(null);
  const appliedConversationIdRef = useRef<string | null>(null);

  useEffect(() => {
    onThreadRuntime(threadRuntime);
  }, [onThreadRuntime, threadRuntime]);

  useEffect(() => {
    if (!conversationDetail) {
      appliedHydrationKeyRef.current = null;
      appliedConversationIdRef.current = null;
      onPrepareResumeExisting('', null);
      threadRuntime.cancelRun();
      threadRuntime.reset([]);
      return;
    }

    // A delayed detail request can resolve after the user has already sent a
    // message.  Hydrating that stale snapshot would call cancelRun/reset and
    // erase the live assistant turn. Keep the local run authoritative for the
    // same conversation. A genuine conversation change is allowed to detach
    // the old local stream once the new detail is ready.
    const isConversationChange = (
      appliedConversationIdRef.current !== null
      && appliedConversationIdRef.current !== conversationDetail.id
    );
    if (isThreadRunning && !isConversationChange) {
      // Record which conversation owns the live runtime even when its stale
      // server snapshot must not be applied. A later id change can then detach
      // this stream safely.
      appliedConversationIdRef.current = conversationDetail.id;
      return;
    }

    const hydrationKey = getConversationHydrationKey(conversationDetail);
    if (appliedHydrationKeyRef.current === hydrationKey) {
      return;
    }
    appliedHydrationKeyRef.current = hydrationKey;
    appliedConversationIdRef.current = conversationDetail.id;

    onPrepareResumeExisting(conversationDetail.id, null);
    threadRuntime.cancelRun();
    threadRuntime.reset([]);

    const isGenerating = conversationDetail.isGenerating === true;
    const canReplayStream = conversationDetail.resumeState?.active === true;
    const threadStateHasRichParts = hasRichParts(conversationDetail.threadState);
    const retainedFinalText = (conversationDetail.resumeState?.assistantText || '').trim();
    const latestStage = conversationDetail.resumeState?.latestStage;
    const lastAssistantText = getLastAssistantText(conversationDetail.messages);
    const retainedTextMismatch = Boolean(
      retainedFinalText && lastAssistantText && retainedFinalText !== lastAssistantText,
    );
    const shouldReplayStream = isGenerating
      || (canReplayStream
        && (
          (conversationDetail.resumeState?.hasToolEvents === true && !threadStateHasRichParts)
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

    // 纯文本历史以 messages 为权威来源。threadState 是 assistant-ui 的内部
    // 导出格式，旧版本或外部写入的精简快照可能缺少 createdAt/metadata 等字段；
    // 无条件 import 会让消息区只剩空白 assistant 气泡。只有工具消息确实需要
    // 保留工具卡片、或思考消息需要保留 reasoning part 时才导入，并在格式
    // 不兼容时可靠回退到标准消息列表。
    const canImportThreadState = !shouldReplayStream
      && threadStateHasRichParts
      && Boolean(conversationDetail.threadState?.messages?.length);
    if (canImportThreadState) {
      try {
        threadRuntime.import(
          withPersistedStage(
            conversationDetail.threadState,
            latestStage,
          ) as unknown as ExportedMessageRepository,
        );
      } catch (error) {
        console.warn('[Chat] Invalid conversation thread state, falling back to messages', error);
        threadRuntime.reset(toRuntimeMessages(visibleMessages, latestStage));
      }
    } else {
      threadRuntime.reset(toRuntimeMessages(visibleMessages, latestStage));
    }

    if (!shouldReplayStream) {
      // A normal send starts its run inside assistant-ui before hydration.  If
      // persisted history ends with a user message while the backend reports
      // no resumable run, it represents a cancelled/failed/interrupted turn.
      // Starting here would silently resurrect Stop requests and can loop on
      // every snapshot refresh.  Only the resumable branch below may auto-run.
      return;
    }

    // 后端仍在生成或保留了刚完成的 run:通过 /agent/chat + resume_existing
    // 复用 useDataStreamRuntime 的完整 data-stream 管道,保证 K 线图等 tool UI
    // 与初次生成一致。
    const parentId = visibleMessages.at(-1)?.id ?? null;
    // The server chooses the cursor. A durable run may intentionally return
    // zero when the browser died before persisting rich tool/reasoning parts;
    // replay then reconstructs those parts from the ordered event log.
    onPrepareResumeExisting(
      conversationDetail.id,
      conversationDetail.resumeState?.afterChunkIndex ?? 0,
    );
    threadRuntime.startRun({
      parentId,
      sourceId: parentId,
      runConfig: {},
    });
  }, [conversationDetail, isThreadRunning, threadRuntime, onPrepareResumeExisting]);

  return null;
};
