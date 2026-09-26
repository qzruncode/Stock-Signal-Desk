import type { FC } from 'react';
import { AuiIf, ComposerPrimitive, useThread } from '@assistant-ui/react';
import {
  ArrowUpIcon,
  MicIcon,
  SquareIcon as StopIcon,
} from 'lucide-react';
import {
  ComposerAddAttachment,
  ComposerAttachmentDropzone,
  ComposerAttachments,
} from './attachment';
import { cn } from '../../utils/cn';
import { AgentModeSelector } from './AgentModeSelector';
import { KnowledgeBaseChatSelector } from './KnowledgeBaseChatSelector';
import type { AgentProductMode } from '../../utils/agentMode';

export const Composer: FC<{
  onUserCancel?: () => void;
  agentMode: AgentProductMode;
  onAgentModeChange: (mode: AgentProductMode) => void;
  knowledgeBaseIds: string[];
  knowledgeSelectionDisabled: boolean;
  onKnowledgeBaseIdsChange: (ids: string[]) => void;
}> = ({
  onUserCancel,
  agentMode,
  onAgentModeChange,
  knowledgeBaseIds,
  knowledgeSelectionDisabled,
  onKnowledgeBaseIdsChange,
}) => {
  const isRunning = useThread((s) => s.isRunning);
  return (
    <div className="shrink-0 border-t border-border/70 bg-background/90 px-3 py-3 backdrop-blur-xl sm:px-4 sm:py-4">
      <ComposerPrimitive.Root className="group/composer relative mx-auto flex w-full max-w-3xl flex-col rounded-xl border border-border/80 bg-card/90 shadow-[0_8px_28px_hsl(220_22%_34%/0.06)] transition focus-within:border-primary/35 focus-within:shadow-[0_10px_32px_hsl(var(--primary)/0.08)]">
        <ComposerAttachmentDropzone />

        <ComposerAttachments />

        {/* 语音输入实时转写预览:录音中在输入框上方显示部分识别结果。 */}
        <AuiIf condition={(s) => s.composer.dictation != null}>
          <div className="flex items-center gap-2 px-3 pt-2 text-xs text-muted-foreground sm:px-4">
            <span className="size-1.5 animate-pulse rounded-full bg-red-500" />
            <ComposerPrimitive.DictationTranscript className="min-w-0 truncate" />
          </div>
        </AuiIf>

        <ComposerPrimitive.Input
          placeholder="问问市场、个股、板块或财务数据..."
          className="chat-composer-input min-h-11 w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-2 text-foreground placeholder-muted-foreground focus:outline-none [scrollbar-gutter:stable] sm:px-4 sm:pt-2.5 sm:pb-1.5"
          minRows={1}
          maxRows={10}
        />

        <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5">
          <div className="flex items-center gap-0.5">
            <KnowledgeBaseChatSelector
              knowledgeBaseIds={knowledgeBaseIds}
              disabled={knowledgeSelectionDisabled || isRunning}
              onKnowledgeBaseIdsChange={onKnowledgeBaseIdsChange}
            />
            <ComposerAddAttachment />
            {/* 语音输入:无 DictationAdapter(浏览器不支持)或非编辑态时 Dictate 自动隐藏;
                录音中显示 StopDictation(红点)+ 实时转写预览。 */}
            <ComposerPrimitive.Dictate
              className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
              title="语音输入"
            >
              <MicIcon className="size-3.5" />
            </ComposerPrimitive.Dictate>
            <AuiIf condition={(s) => s.composer.dictation != null}>
              <ComposerPrimitive.StopDictation
                className="relative flex size-8 items-center justify-center rounded-lg text-red-500 transition hover:bg-red-50"
                title="停止语音输入"
              >
                <MicIcon className="size-3.5" />
                <span className="absolute right-1.5 top-1.5 size-1.5 animate-pulse rounded-full bg-red-500" />
              </ComposerPrimitive.StopDictation>
            </AuiIf>
            <AgentModeSelector
              value={agentMode}
              onChange={onAgentModeChange}
              disabled={isRunning}
            />
          </div>

          {isRunning ? (
            <ComposerPrimitive.Cancel
              onClick={onUserCancel}
              className={cn(
                'flex size-8 items-center justify-center rounded-lg',
                'border border-border bg-card text-foreground shadow-sm transition',
                'hover:bg-muted disabled:opacity-30',
              )}
              title="停止生成"
            >
              <StopIcon className="size-3" />
            </ComposerPrimitive.Cancel>
          ) : (
            <ComposerPrimitive.Send
              className={cn(
                'flex size-8 items-center justify-center rounded-lg',
                'bg-primary/90 text-primary-foreground shadow-sm transition',
                'hover:bg-primary disabled:opacity-30 disabled:shadow-none',
              )}
            >
              <ArrowUpIcon className="size-3.5" />
            </ComposerPrimitive.Send>
          )}
        </div>
      </ComposerPrimitive.Root>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] leading-4 text-muted-foreground/80">
        AI 可能出错，关键投资事实请结合原始公告与数据来源复核；内容不构成投资建议。
      </p>
    </div>
  );
};
