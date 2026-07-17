import type { FC } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { EllipsisIcon, MessageSquareIcon, PencilIcon, PlusIcon, SearchIcon, Trash2Icon } from 'lucide-react';
import type { ChatConversationItem } from '../../api/agent';
import { cn } from '../../utils/cn';
import { TooltipIconButton } from './tooltip-icon-button';

export interface ThreadListSidebarProps {
  side?: 'left' | 'right';
  className?: string;
  conversations: ChatConversationItem[];
  selectedConversationId: string | null;
  isLoading?: boolean;
  onCreate: () => void;
  onSelect: (conversationId: string) => void;
  onRename: (conversation: ChatConversationItem) => void;
  onDelete: (conversation: ChatConversationItem) => void;
  onBatchDelete: (conversationIds: string[]) => void;
}

export const ThreadListSidebar: FC<ThreadListSidebarProps> = ({
  side = 'left',
  className,
  conversations,
  selectedConversationId,
  isLoading = false,
  onCreate,
  onSelect,
  onRename,
  onDelete,
  onBatchDelete,
}) => {
  const [isTouchMode, setIsTouchMode] = useState(false);
  const [actionConversation, setActionConversation] = useState<ChatConversationItem | null>(null);
  const [actionMenuPosition, setActionMenuPosition] = useState<{ top: number; left: number } | null>(null);
  const [isBatchMode, setIsBatchMode] = useState(false);
  const [selectedConversationIds, setSelectedConversationIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const sidebarRef = useRef<HTMLDivElement | null>(null);
  const longPressTimerRef = useRef<number | null>(null);
  const longPressTriggeredRef = useRef(false);

  useEffect(() => {
    if (typeof window === 'undefined') {
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

  const exitBatchMode = () => {
    setIsBatchMode(false);
    setSelectedConversationIds([]);
  };

  const toggleConversationSelection = (conversationId: string) => {
    setSelectedConversationIds((current) => (
      current.includes(conversationId)
        ? current.filter((id) => id !== conversationId)
        : [...current, conversationId]
    ));
  };

  const allSelected = conversations.length > 0 && selectedConversationIds.length === conversations.length;
  const visibleConversations = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase();
    if (!query) return conversations;
    return conversations.filter((conversation) => (
      `${conversation.title || ''} ${conversation.previewText || ''}`.toLocaleLowerCase().includes(query)
    ));
  }, [conversations, searchQuery]);

  const handleSelectConversation = (conversationId: string) => {
    if (isBatchMode) {
      toggleConversationSelection(conversationId);
      return;
    }
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
        'relative flex h-full w-full shrink-0 flex-col bg-card',
        side === 'left' ? 'border-r border-border' : 'border-l border-border',
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">对话列表</h2>
        <div className="flex items-center gap-1">
          {conversations.length > 0 ? (
            <button
              type="button"
              onClick={() => {
                if (isBatchMode) {
                  exitBatchMode();
                  return;
                }
                setIsBatchMode(true);
              }}
              className={cn(
                'rounded-lg px-2.5 py-1.5 text-xs transition',
                isBatchMode ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              {isBatchMode ? '取消' : '批量管理'}
            </button>
          ) : null}
          <TooltipIconButton tooltip="新建对话" className="size-8" onClick={onCreate}>
            <PlusIcon className="size-4" />
          </TooltipIconButton>
        </div>
      </div>

      <div className="border-b border-border px-3 py-2.5">
        <label className="flex items-center gap-2 rounded-lg border border-border bg-background px-2.5 py-2 text-muted-foreground focus-within:border-primary/40 focus-within:text-foreground">
          <SearchIcon className="size-3.5 shrink-0" />
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="搜索对话"
            className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
      </div>

      {isBatchMode ? (
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
          <button
            type="button"
            onClick={() => {
              if (allSelected) {
                setSelectedConversationIds([]);
                return;
              }
              setSelectedConversationIds(conversations.map((conversation) => conversation.id));
            }}
            className="text-xs text-muted-foreground transition hover:text-foreground"
          >
            {allSelected ? '取消全选' : '全选'}
          </button>
          <button
            type="button"
            disabled={selectedConversationIds.length === 0}
            onClick={() => {
              onBatchDelete(selectedConversationIds);
              exitBatchMode();
            }}
            className={cn(
              'rounded-lg px-2.5 py-1.5 text-xs transition',
              selectedConversationIds.length === 0
                ? 'cursor-not-allowed text-muted-foreground/50'
                : 'bg-red-50 text-red-600 hover:bg-red-100',
            )}
          >
            删除选中（{selectedConversationIds.length}）
          </button>
        </div>
      ) : null}

      <div className="flex flex-1 flex-col gap-1 overflow-y-auto p-2">
        {isLoading ? (
          <div className="px-3 py-6 text-xs text-muted-foreground">加载对话中...</div>
        ) : null}

        {!isLoading && conversations.length === 0 ? (
          <div className="px-3 py-6 text-xs text-muted-foreground">还没有对话，先新建一个。</div>
        ) : null}

        {!isLoading && conversations.length > 0 && visibleConversations.length === 0 ? (
          <div className="px-3 py-6 text-xs text-muted-foreground">没有匹配的对话。</div>
        ) : null}

        {visibleConversations.map((conversation) => {
          const isActive = conversation.id === selectedConversationId;
          return (
            <div
              key={conversation.id}
              data-conversation-row="true"
              className={cn(
                'group/item flex items-center gap-2 rounded-lg px-3 py-2 transition-colors',
                isBatchMode && selectedConversationIds.includes(conversation.id) && 'bg-accent/80',
                isActive ? 'bg-accent' : 'hover:bg-accent/60',
              )}
              onContextMenu={(event) => {
                if (isBatchMode) {
                  return;
                }
                event.preventDefault();
                openConversationActions(conversation, event.currentTarget.getBoundingClientRect());
              }}
            >
              {isBatchMode ? (
                <button
                  type="button"
                  onClick={() => toggleConversationSelection(conversation.id)}
                  className={cn(
                    'flex size-4 shrink-0 items-center justify-center rounded border transition',
                    selectedConversationIds.includes(conversation.id)
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-background text-transparent',
                  )}
                  aria-label={selectedConversationIds.includes(conversation.id) ? '取消选择' : '选择对话'}
                >
                  <span className="text-[10px] leading-none">✓</span>
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => handleSelectConversation(conversation.id)}
                onTouchStart={(event) => {
                  if (isBatchMode) {
                    return;
                  }
                  scheduleLongPress(conversation, event.currentTarget.closest('[data-conversation-row="true"]') as HTMLDivElement | null);
                }}
                onTouchEnd={clearLongPressTimer}
                onTouchCancel={clearLongPressTimer}
                onTouchMove={clearLongPressTimer}
                className="flex min-w-0 flex-1 items-center gap-2 truncate text-left text-sm"
              >
                <MessageSquareIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className={cn('truncate text-foreground/80', isActive && 'font-medium text-foreground')}>
                  {conversation.title || '新对话'}
                </span>
              </button>
              <TooltipIconButton
                tooltip="重命名"
                className={cn(itemActionButtonClassName, (isTouchMode || isBatchMode) && 'hidden')}
                onClick={() => onRename(conversation)}
              >
                <PencilIcon className="size-3.5" />
              </TooltipIconButton>
              <TooltipIconButton
                tooltip="删除对话"
                className={cn(itemActionButtonClassName, (isTouchMode || isBatchMode) && 'hidden')}
                onClick={() => onDelete(conversation)}
              >
                <Trash2Icon className="size-3.5" />
              </TooltipIconButton>
              {isTouchMode && !isBatchMode ? (
                <button
                  type="button"
                  className={cn(
                    'inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition',
                    'hover:bg-accent hover:text-foreground',
                  )}
                  onClick={(event) => openConversationActions(conversation, event.currentTarget.getBoundingClientRect())}
                  aria-label="更多操作"
                >
                  <EllipsisIcon className="size-4" />
                </button>
              ) : null}
            </div>
          );
        })}
      </div>

      {actionConversation && actionMenuPosition ? (
        <div
          className="absolute inset-0 z-40 bg-black/15"
          onClick={dismissConversationActions}
        >
          <div
            className="absolute w-43 rounded-xl border border-border bg-card p-1.5 shadow-2xl"
            style={{ top: actionMenuPosition.top, left: actionMenuPosition.left }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => {
                dismissConversationActions();
                onRename(actionConversation);
              }}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-foreground transition hover:bg-accent"
            >
              <PencilIcon className="size-4 text-muted-foreground" />
              <span>重命名</span>
            </button>

            <button
              type="button"
              onClick={() => {
                dismissConversationActions();
                onDelete(actionConversation);
              }}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-red-600 transition hover:bg-red-50"
            >
              <Trash2Icon className="size-4" />
              <span>删除对话</span>
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
};
