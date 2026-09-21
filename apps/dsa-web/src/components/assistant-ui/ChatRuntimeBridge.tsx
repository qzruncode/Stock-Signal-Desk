import type React from 'react';
import { useEffect, useRef } from 'react';
import { useThread, useThreadRuntime } from '@assistant-ui/react';
import type { ChatConversationDetail } from '../../api/agent';
import {
  getConversationHydrationKey,
  isTerminalRunStatus,
  PENDING_ASSISTANT_SUFFIX,
  removeTrailingAssistant,
  toRuntimeMessages,
} from './ChatRuntimeBridgeUtils';

/**
 * Bridges persisted conversation state into assistant-ui's ThreadRuntime.
 * Hydration and resume decisions stay here; pure message conversion lives in
 * ChatRuntimeBridgeUtils so it can be tested without mounting the runtime.
 */

export type ChatRuntimeBridgeProps = {
  conversationDetail: ChatConversationDetail | null;
  onThreadRuntime: (threadRuntime: ReturnType<typeof useThreadRuntime>) => void;
  onPrepareResumeExisting: (conversationId: string, afterChunkIndex: number | null) => void;
  /**
   * A terminal snapshot is only allowed to detach a local stream that is
   * actually attached to that durable run. A user can send a new turn while
   * the selected detail still describes the preceding terminal run.
   */
  shouldDetachTerminalStream?: (conversationId: string, runId: string | null | undefined) => boolean;
};

const detachTerminalStreamByDefault = () => true;

export const ChatRuntimeBridge: React.FC<ChatRuntimeBridgeProps> = ({
  conversationDetail,
  onThreadRuntime,
  onPrepareResumeExisting,
  shouldDetachTerminalStream = detachTerminalStreamByDefault,
}) => {
  const threadRuntime = useThreadRuntime();
  const isThreadRunning = useThread((state) => state.isRunning);
  const appliedHydrationKeyRef = useRef<string | null>(null);
  const appliedConversationIdRef = useRef<string | null>(null);
  const pendingConversationIdRef = useRef<string | null>(null);
  const terminalDetachKeyRef = useRef<string | null>(null);

  useEffect(() => {
    onThreadRuntime(threadRuntime);
  }, [onThreadRuntime, threadRuntime]);

  useEffect(() => {
    if (!conversationDetail) {
      appliedHydrationKeyRef.current = null;
      appliedConversationIdRef.current = null;
      pendingConversationIdRef.current = null;
      terminalDetachKeyRef.current = null;
      onPrepareResumeExisting('', null);
      threadRuntime.cancelRun();
      threadRuntime.reset([]);
      return;
    }

    // A delayed detail request can resolve after the user has already sent a
    // message. Hydrating that stale snapshot would erase the live turn.
    const isConversationChange = (
      appliedConversationIdRef.current !== null
      && appliedConversationIdRef.current !== conversationDetail.id
    );
    const hydrationKey = getConversationHydrationKey(conversationDetail);
    const serverReportedTerminal = isTerminalRunStatus(
      conversationDetail.resumeState?.status,
    );
    if (isThreadRunning && isConversationChange) {
      if (pendingConversationIdRef.current !== conversationDetail.id) {
        pendingConversationIdRef.current = conversationDetail.id;
        onPrepareResumeExisting('', null);
        threadRuntime.cancelRun();
      }
      return;
    }
    if (isThreadRunning && !isConversationChange) {
      if (
        serverReportedTerminal
        && shouldDetachTerminalStream(
          conversationDetail.id,
          conversationDetail.resumeState?.runId,
        )
      ) {
        if (terminalDetachKeyRef.current !== hydrationKey) {
          terminalDetachKeyRef.current = hydrationKey;
          threadRuntime.cancelRun();
        }
        return;
      }
      appliedConversationIdRef.current = conversationDetail.id;
      return;
    }

    terminalDetachKeyRef.current = null;
    if (appliedHydrationKeyRef.current === hydrationKey) {
      return;
    }
    appliedHydrationKeyRef.current = hydrationKey;
    appliedConversationIdRef.current = conversationDetail.id;
    pendingConversationIdRef.current = null;

    onPrepareResumeExisting(conversationDetail.id, null);
    threadRuntime.cancelRun();
    threadRuntime.reset([]);

    const isGenerating = conversationDetail.isGenerating === true;
    const isWaitingForApproval = Boolean(conversationDetail.pendingInterrupt);
    const resumeStatus = conversationDetail.resumeState?.status;
    const canReplayStream = !isTerminalRunStatus(resumeStatus) && (
      isGenerating || conversationDetail.resumeState?.active === true
    );
    const latestStage = conversationDetail.resumeState?.latestStage;
    const executionTrace = conversationDetail.executionTrace;
    const shouldReplayStream = !isWaitingForApproval && canReplayStream;
    const pendingId = `${conversationDetail.id}${PENDING_ASSISTANT_SUFFIX}`;
    const messagesWithoutPending = conversationDetail.messages.filter((m) => m.id !== pendingId);
    const visibleMessages = shouldReplayStream
      ? removeTrailingAssistant(messagesWithoutPending)
      : conversationDetail.messages;

    threadRuntime.reset(toRuntimeMessages(
      conversationDetail.id,
      visibleMessages,
      latestStage,
      executionTrace,
      conversationDetail.resumeState?.runId,
      conversationDetail.resumeState?.assistantText,
      !shouldReplayStream,
      conversationDetail.executionTraces,
    ));

    if (!shouldReplayStream) {
      return;
    }

    const parentId = visibleMessages.at(-1)?.id ?? null;
    onPrepareResumeExisting(
      conversationDetail.id,
      conversationDetail.resumeState?.afterChunkIndex ?? 0,
    );
    threadRuntime.startRun({
      parentId,
      sourceId: parentId,
      runConfig: {},
    });
  }, [
    conversationDetail,
    isThreadRunning,
    threadRuntime,
    onPrepareResumeExisting,
    shouldDetachTerminalStream,
  ]);

  return null;
};
