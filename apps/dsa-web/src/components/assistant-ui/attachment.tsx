import type { FC } from 'react';
import {
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
} from '@assistant-ui/react';
import { FileIcon, PaperclipIcon, XIcon } from 'lucide-react';
import { cn } from '../../utils/cn';
import { TooltipIconButton } from './tooltip-icon-button';

/* ── Composer Attachments (preview area above input) ──────────────────── */

export const ComposerAttachments: FC = () => (
  <ComposerPrimitive.Attachments
    components={{
      Image: ComposerAttachmentItem,
      Document: ComposerAttachmentItem,
      File: ComposerAttachmentItem,
    }}
  />
);

const ComposerAttachmentItem: FC = () => (
  <AttachmentPrimitive.Root
    className={cn(
      'group/attachment relative flex items-center gap-2',
      'rounded-lg border border-border bg-muted/50 px-3 py-1.5 text-xs',
    )}
  >
    <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
    <AttachmentPrimitive.Name />
    <AttachmentPrimitive.Remove asChild>
      <button
        type="button"
        className="ml-0.5 text-muted-foreground opacity-0 group-hover/attachment:opacity-100 hover:text-destructive transition-colors"
      >
        <XIcon className="size-3" />
      </button>
    </AttachmentPrimitive.Remove>
  </AttachmentPrimitive.Root>
);

/* ── Add Attachment Button (paperclip icon in composer) ───────────────── */

export const ComposerAddAttachment: FC = () => (
  <ComposerPrimitive.AddAttachment asChild>
    <TooltipIconButton tooltip="添加附件" className="size-8">
      <PaperclipIcon className="size-4" />
    </TooltipIconButton>
  </ComposerPrimitive.AddAttachment>
);

/* ── Attachment Dropzone (drag-and-drop overlay) ──────────────────────── */

export const ComposerAttachmentDropzone: FC = () => (
  <ComposerPrimitive.AttachmentDropzone className="absolute inset-0 rounded-3xl border-2 border-dashed border-primary/50 bg-primary/5 opacity-0 data-[dragging]:opacity-100 transition-opacity pointer-events-none" />
);

/* ── User Message Attachments ─────────────────────────────────────────── */

export const UserMessageAttachments: FC = () => (
  <MessagePrimitive.Attachments
    components={{
      Image: UserMessageImageAttachment,
      Document: UserMessageFileAttachment,
      File: UserMessageFileAttachment,
    }}
  />
);

const UserMessageImageAttachment: FC = () => (
  <AttachmentPrimitive.Root className="overflow-hidden rounded-lg border border-primary-foreground/20">
    <AttachmentPrimitive.unstable_Thumb className="max-h-48 max-w-full object-contain" />
  </AttachmentPrimitive.Root>
);

const UserMessageFileAttachment: FC = () => (
  <AttachmentPrimitive.Root
    className={cn(
      'inline-flex items-center gap-1.5 rounded-lg',
      'border border-primary-foreground/20 bg-primary-foreground/10',
      'px-2.5 py-1.5 text-xs text-primary-foreground',
    )}
  >
    <FileIcon className="size-3" />
    <AttachmentPrimitive.Name />
  </AttachmentPrimitive.Root>
);
