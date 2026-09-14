import type { ClipboardEvent, FC } from 'react';
import {
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  useAui,
  useAuiState,
  useMessage,
} from '@assistant-ui/react';
import type { TextMessagePartProps } from '@assistant-ui/react';
import { PencilIcon, Trash2Icon } from 'lucide-react';
import { UserMessageAttachments } from './attachment';
import { cn } from '../../utils/cn';
import { getChatQuestionDomId } from '../../utils/chatQuestionLocator';

const UserMessageTextPart: FC<TextMessagePartProps> = ({ text }) => (
  <span className="whitespace-pre-wrap break-words">{text}</span>
);

const normalizeUserMessageClipboardText = (text: string): string => (
  text.replace(/\r\n?/g, '\n').replace(/\n+$/g, '')
);

const EditComposerSendButton: FC = () => {
  const aui = useAui();
  const messageId = useMessage((state) => state.id);
  const isEmpty = useAuiState((s) => s.composer.isEmpty);

  return (
    <button
      type="button"
      className="flex h-8 items-center rounded-lg bg-primary px-3 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:opacity-30"
      title="重新发送"
      disabled={isEmpty}
      onClick={() => {
        const composer = aui.composer();
        const runConfig = composer.getState().runConfig;
        composer.setRunConfig({
          ...runConfig,
          custom: {
            ...(runConfig.custom || {}),
            editMessageId: messageId,
          },
        });
        composer.send({ startRun: true });
      }}
    >
      发送
    </button>
  );
};

export const UserMessage: FC<{ onDeleteTurn?: (messageId: string) => void }> = ({ onDeleteTurn }) => {
  const messageId = useMessage((state) => state.id);

  const handleCopy = (event: ClipboardEvent<HTMLDivElement>) => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;

    const selectedText = selection.toString();
    const normalizedText = normalizeUserMessageClipboardText(selectedText);
    if (normalizedText === selectedText) return;

    event.preventDefault();
    event.clipboardData.setData('text/plain', normalizedText);
  };

  return (
    <MessagePrimitive.Root
      className="group/message mb-1.5 flex w-full min-w-0 items-start justify-end"
      onCopyCapture={handleCopy}
    >
      <div className="relative flex min-w-0 max-w-[82%] flex-col items-end pb-6 sm:max-w-[68%] sm:pb-5">
        <UserMessageAttachments />
        {/* 编辑态:点击 Edit 后 composer.isEditing=true,这里渲染编辑输入框;
            ComposerPrimitive 在 message 上下文下会自动绑定到该消息的 edit composer,
            提交(Send)即覆盖原消息并重新生成。 */}
        <AuiIf condition={(s) => s.composer.isEditing}>
          <ComposerPrimitive.Root className="w-full rounded-2xl rounded-br-md border border-primary/30 bg-card shadow-sm focus-within:border-primary/45">
            <ComposerPrimitive.Input
              autoFocus
              className="chat-composer-input min-h-14 w-full resize-none bg-transparent px-4 py-3 text-foreground placeholder-muted-foreground focus:outline-none [overflow-wrap:anywhere]"
            />
            <div className="flex items-center justify-end gap-2 px-3 pb-3">
              <ComposerPrimitive.Cancel
                className="flex h-8 items-center rounded-lg border border-border bg-card px-3 text-xs text-foreground transition hover:bg-muted"
                title="取消编辑"
              >
                取消
              </ComposerPrimitive.Cancel>
              <EditComposerSendButton />
            </div>
          </ComposerPrimitive.Root>
        </AuiIf>
        {/* 非编辑态:静态气泡 + Edit/Delete 按钮 */}
        <AuiIf condition={(s) => !s.composer.isEditing}>
          <div
            id={getChatQuestionDomId(messageId)}
            data-chat-question-bubble="true"
            tabIndex={-1}
            className={cn(
              'min-w-0 max-w-full scroll-mt-16 overflow-hidden rounded-xl rounded-br-md bg-primary/[0.06] px-3 py-2 text-[17px] leading-7 text-foreground outline-none [overflow-wrap:anywhere]',
              'transition-[background-color,box-shadow] duration-300 data-[chat-question-located=true]:bg-primary/[0.12] data-[chat-question-located=true]:shadow-[0_0_0_3px_hsl(var(--primary)/0.28)]',
            )}
          >
            <MessagePrimitive.Parts components={{ Text: UserMessageTextPart }} />
          </div>
          <div className="absolute bottom-0 right-0 flex h-6 items-center gap-0.5 opacity-100 transition-opacity sm:h-5 sm:opacity-0 sm:group-hover/message:opacity-100 sm:focus-within:opacity-100">
            <button
              type="button"
              className="flex size-6 items-center justify-center rounded text-muted-foreground transition hover:bg-red-50 hover:text-red-600 sm:size-5"
              title="删除这一轮对话"
              aria-label="删除这一轮对话"
              onClick={() => onDeleteTurn?.(messageId)}
            >
              <Trash2Icon className="size-3.5 sm:size-3" />
            </button>
            <ActionBarPrimitive.Edit
              className="flex size-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground sm:size-5"
              title="编辑并重新发送"
            >
              <PencilIcon className="size-3.5 sm:size-3" />
            </ActionBarPrimitive.Edit>
          </div>
        </AuiIf>
      </div>
    </MessagePrimitive.Root>
  );
};
