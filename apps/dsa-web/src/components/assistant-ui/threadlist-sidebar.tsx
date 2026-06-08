import type { FC } from 'react';
import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
} from '@assistant-ui/react';
import { ArchiveIcon, PlusIcon, MessageSquareIcon } from 'lucide-react';
import { TooltipIconButton } from './tooltip-icon-button';
import { cn } from '../../utils/cn';

/* ── ThreadListSidebar ────────────────────────────────────────────────── */

export const ThreadListSidebar: FC = () => {
  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-card">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">对话列表</h2>
        <ThreadListNew />
      </div>

      {/* Thread list */}
      <ThreadListPrimitive.Root className="flex flex-1 flex-col gap-1 overflow-y-auto p-2">
        <ThreadListItems />
      </ThreadListPrimitive.Root>
    </div>
  );
};

/* ── New Thread Button ────────────────────────────────────────────────── */

const ThreadListNew: FC = () => (
  <ThreadListPrimitive.New asChild>
    <TooltipIconButton tooltip="新建对话" className="size-8">
      <PlusIcon className="size-4" />
    </TooltipIconButton>
  </ThreadListPrimitive.New>
);

/* ── Thread List Items ────────────────────────────────────────────────── */

const ThreadListItems: FC = () => (
  <ThreadListPrimitive.Items components={{ ThreadListItem }} />
);

/* ── Single Thread List Item ──────────────────────────────────────────── */

const ThreadListItem: FC = () => (
  <ThreadListItemPrimitive.Root
    className={cn(
      'group/item flex items-center gap-2 rounded-lg px-3 py-2 transition-colors',
      'data-[active]:bg-accent',
      'hover:bg-accent/60',
    )}
  >
    <ThreadListItemPrimitive.Trigger className="flex flex-1 items-center gap-2 truncate text-left text-sm">
      <MessageSquareIcon className="size-4 shrink-0 text-muted-foreground" />
      <ThreadListItemTitle />
    </ThreadListItemPrimitive.Trigger>
    <ThreadListItemArchive />
  </ThreadListItemPrimitive.Root>
);

/* ── Thread Title ─────────────────────────────────────────────────────── */

const ThreadListItemTitle: FC = () => (
  <span className="truncate text-foreground/80 group-data-[active]/item:text-foreground group-data-[active]/item:font-medium">
    <ThreadListItemPrimitive.Title fallback="新对话" />
  </span>
);

/* ── Archive Button ───────────────────────────────────────────────────── */

const ThreadListItemArchive: FC = () => (
  <ThreadListItemPrimitive.Archive asChild>
    <TooltipIconButton
      tooltip="归档对话"
      className="size-6 opacity-0 group-hover/item:opacity-100"
    >
      <ArchiveIcon className="size-3.5" />
    </TooltipIconButton>
  </ThreadListItemPrimitive.Archive>
);
