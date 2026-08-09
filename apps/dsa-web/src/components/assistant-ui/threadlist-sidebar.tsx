import type { FC } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  EllipsisIcon,
  Loader2Icon,
  MessageSquareIcon,
  MessageSquarePlusIcon,
  PanelLeftCloseIcon,
  PencilIcon,
  SearchIcon,
  SettingsIcon,
  SparklesIcon,
  Trash2Icon,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import type { ChatConversationItem } from '../../api/agent';
import { cn } from '../../utils/cn';
import { TooltipIconButton } from './tooltip-icon-button';

export interface ThreadListSidebarProps {
  side?: 'left' | 'right';
  className?: string;
  conversations: ChatConversationItem[];
  selectedConversationId: string | null;
  isLoading?: boolean;
  loadingConversationId?: string | null;
  onCreate: () => void;
  onSelect: (conversationId: string) => void;
  onRename: (conversation: ChatConversationItem) => void;
  onDelete: (conversation: ChatConversationItem) => void;
  onClearAll: () => void;
  isClearingAll?: boolean;
  onCollapse?: () => void;
}

export const ThreadListSidebar: FC<ThreadListSidebarProps> = ({
  side = 'left',
  className,
  conversations,
  selectedConversationId,
  isLoading = false,
  loadingConversationId = null,
  onCreate,
  onSelect,
  onRename,
  onDelete,
  onClearAll,
  isClearingAll = false,
  onCollapse,
}) => {
  const [isTouchMode, setIsTouchMode] = useState(false);
  const [actionConversation, setActionConversation] = useState<ChatConversationItem | null>(null);
  const [actionMenuPosition, setActionMenuPosition] = useState<{ top: number; left: number } | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const sidebarRef = useRef<HTMLDivElement | null>(null);
  const longPressTimerRef = useRef<number | null>(null);
  const longPressTriggeredRef = useRef(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const mediaQuery = window.matchMedia('(hover: none), (pointer: coarse)');
    const syncTouchMode = () => setIsTouchMode(mediaQuery.matches);
    syncTouchMode();
    mediaQuery.addEventListener('change', syncTouchMode);
    return () => mediaQuery.removeEventListener('change', syncTouchMode);
  }, []);

  const itemActionButtonClassName = useMemo(
    () => (
      isTouchMode
        ? 'size-7 opacity-100'
        : 'size-6 opacity-0 group-hover/item:opacity-100 group-focus-within/item:opacity-100'
    ),
    [isTouchMode],
  );

  const clearLongPressTimer = () => {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };

  const openConversationActions = (
    conversation: ChatConversationItem,
    anchorRect?: DOMRect | null,
  ) => {
    clearLongPressTimer();
    longPressTriggeredRef.current = true;
    setActionConversation(conversation);
    if (anchorRect) {
      const menuWidth = 172;
      const sidebarRect = sidebarRef.current?.getBoundingClientRect() ?? null;
      if (sidebarRect) {
        const padding = 12;
        const minLeft = padding;
        const maxLeft = Math.max(padding, sidebarRect.width - menuWidth - padding);
        const targetLeft = anchorRect.right - sidebarRect.left - menuWidth;
        const left = Math.min(Math.max(targetLeft, minLeft), maxLeft);
        const top = Math.min(
          Math.max(anchorRect.bottom - sidebarRect.top + 6, padding),
          Math.max(padding, sidebarRect.height - 96),
        );
        setActionMenuPosition({ top, left });
        return;
      }
    }
    if (typeof window !== 'undefined' && anchorRect) {
      const menuWidth = 172;
      const viewportPadding = 12;
      const left = Math.min(Math.max(anchorRect.right - menuWidth, viewportPadding), window.innerWidth - menuWidth - viewportPadding);
      const top = Math.min(anchorRect.bottom + 6, window.innerHeight - 96);
      setActionMenuPosition({ top, left });
      return;
    }
    setActionMenuPosition(null);
  };

  const scheduleLongPress = (conversation: ChatConversationItem, rowElement: HTMLDivElement | null) => {
    clearLongPressTimer();
    longPressTriggeredRef.current = false;
    longPressTimerRef.current = window.setTimeout(() => {
      openConversationActions(conversation, rowElement?.getBoundingClientRect() ?? null);
    }, 420);
  };

  const dismissConversationActions = () => {
    setActionConversation(null);
    setActionMenuPosition(null);
  };

  const visibleConversations = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase();
    if (!query) return conversations;
    return conversations.filter((conversation) => (
      `${conversation.title || ''} ${conversation.previewText || ''}`.toLocaleLowerCase().includes(query)
    ));
  }, [conversations, searchQuery]);

  const handleSelectConversation = (conversationId: string) => {
    if (longPressTriggeredRef.current) {
      longPressTriggeredRef.current = false;
      return;
    }
    onSelect(conversationId);
  };

  return (
    <div
      ref={sidebarRef}
      data-sidebar-root="thread-list"
      className={cn(
        'relative flex h-full w-full shrink-0 flex-col bg-white text-[12px]',
        side === 'left' ? 'border-r border-border/80' : 'border-l border-border/80',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3 py-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <div className="flex size-6 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-600">
            <SparklesIcon className="size-3.5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-[11px] font-semibold leading-4 text-foreground">Stock Assistant</p>
            <p className="truncate text-[9px] font-medium uppercase leading-3 tracking-[0.12em] text-emerald-600">Research</p>
          </div>
        </div>
        <div className="flex items-center gap-0.5">
          {conversations.length > 0 ? (
            <TooltipIconButton
              tooltip="清除全部会话历史"
              className="size-7 rounded-md text-red-500 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={isClearingAll}
              onClick={onClearAll}
            >
              {isClearingAll ? <Loader2Icon className="size-3.5 animate-spin motion-reduce:animate-none" /> : <Trash2Icon className="size-3.5" />}
            </TooltipIconButton>
          ) : null}
          <TooltipIconButton tooltip="新建对话" className="size-7 rounded-md" disabled={isClearingAll} onClick={onCreate}>
            <MessageSquarePlusIcon className="size-3.5" />
          </TooltipIconButton>
          {onCollapse ? (
            <TooltipIconButton tooltip="收起对话列表" className="size-7 rounded-md" onClick={onCollapse}>
              <PanelLeftCloseIcon className="size-3.5" />
            </TooltipIconButton>
          ) : null}
        </div>
      </div>

      <div className="border-b border-border/70 px-3 py-2">
        <label className="flex items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 py-1.5 text-muted-foreground focus-within:border-emerald-400/70 focus-within:bg-white focus-within:text-emerald-700">
          <SearchIcon className="size-3 shrink-0" />
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="搜索对话"
            className="min-w-0 flex-1 bg-transparent text-[11px] leading-4 text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
      </div>

      <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-1.5">
        {isLoading ? (
          <div className="px-2 py-5 text-[11px] text-muted-foreground">加载对话中...</div>
        ) : null}

        {!isLoading && conversations.length === 0 ? (
          <div className="px-2 py-5 text-[11px] text-muted-foreground">还没有对话，先新建一个。</div>
        ) : null}

        {!isLoading && conversations.length > 0 && visibleConversations.length === 0 ? (
          <div className="px-2 py-5 text-[11px] text-muted-foreground">没有匹配的对话。</div>
        ) : null}

        {visibleConversations.map((conversation) => {
          const isActive = conversation.id === selectedConversationId;
          const isLoadingConversation = conversation.id === loadingConversationId;
          return (
            <div
              key={conversation.id}
              data-conversation-row="true"
              className={cn(
                'group/item relative flex items-center gap-1.5 rounded-md border-l-2 border-transparent px-2 py-1.5 transition-colors',
                isActive ? 'border-emerald-500 bg-emerald-50/90 text-emerald-800' : 'hover:bg-muted/70',
              )}
              onContextMenu={(event) => {
                event.preventDefault();
                openConversationActions(conversation, event.currentTarget.getBoundingClientRect());
              }}
            >
              <button
                type="button"
                onClick={() => handleSelectConversation(conversation.id)}
                onTouchStart={(event) => {
                  scheduleLongPress(conversation, event.currentTarget.closest('[data-conversation-row="true"]') as HTMLDivElement | null);
                }}
                onTouchEnd={clearLongPressTimer}
                onTouchCancel={clearLongPressTimer}
                onTouchMove={clearLongPressTimer}
                aria-busy={isLoadingConversation || undefined}
                aria-current={isActive ? 'true' : undefined}
                className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-left text-[12px] leading-5"
              >
                {isLoadingConversation ? (
                  <Loader2Icon className="size-3.5 shrink-0 animate-spin text-emerald-600 motion-reduce:animate-none" />
                ) : (
                  <MessageSquareIcon className={cn('size-3.5 shrink-0', isActive ? 'text-emerald-600' : 'text-muted-foreground/70')} />
                )}
                <span className={cn('truncate text-foreground/72', isActive && 'font-medium text-emerald-800')}>
                  {conversation.title || '新对话'}
                </span>
              </button>
              <TooltipIconButton
                tooltip="重命名"
                className={cn(itemActionButtonClassName, isTouchMode && 'hidden')}
                disabled={isClearingAll}
                onClick={() => onRename(conversation)}
              >
                <PencilIcon className="size-3.5" />
              </TooltipIconButton>
              <TooltipIconButton
                tooltip="删除对话"
                className={cn(itemActionButtonClassName, isTouchMode && 'hidden')}
                disabled={isClearingAll}
                onClick={() => onDelete(conversation)}
              >
                <Trash2Icon className="size-3.5" />
              </TooltipIconButton>
              {isTouchMode ? (
                <button
                  type="button"
                  className={cn(
                    'inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition',
                    'hover:bg-muted hover:text-foreground',
                  )}
                  disabled={isClearingAll}
                  onClick={(event) => openConversationActions(conversation, event.currentTarget.getBoundingClientRect())}
                  aria-label="更多操作"
                >
                  <EllipsisIcon className="size-3.5" />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-1 border-t border-border/70 bg-white px-3 py-2">
        <Link
          to="/setting"
          viewTransition
          className="group inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-all duration-200 hover:bg-muted hover:text-foreground active:scale-90"
          aria-label="AI 助手设置"
          title="AI 助手设置"
        >
          <SettingsIcon className="size-3.5 transition-transform duration-300 group-hover:rotate-45 group-active:rotate-90" />
        </Link>
      </div>

      {actionConversation && actionMenuPosition ? (
        <div
          className="absolute inset-0 z-40 bg-black/15"
          onClick={dismissConversationActions}
        >
          <div
            className="absolute w-36 rounded-lg border border-border bg-white p-1 shadow-xl"
            style={{ top: actionMenuPosition.top, left: actionMenuPosition.left }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => {
                dismissConversationActions();
                onRename(actionConversation);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-foreground transition hover:bg-muted"
            >
              <PencilIcon className="size-3.5 text-muted-foreground" />
              <span>重命名</span>
            </button>

            <button
              type="button"
              onClick={() => {
                dismissConversationActions();
                onDelete(actionConversation);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-red-600 transition hover:bg-red-50"
            >
              <Trash2Icon className="size-3.5" />
              <span>删除对话</span>
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};
