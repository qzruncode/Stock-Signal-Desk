import React, { useState } from 'react';
import { Rss, Trash2, GripVertical } from 'lucide-react';
import type { RssSubscription } from '../../api/rss';
import { EmptyState } from '../common';
import { cn } from '../../utils/cn';

export interface SubscriptionListProps {
  subscriptions: RssSubscription[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (sub: RssSubscription) => void;
  onDelete: (sub: RssSubscription) => void;
  onReorder: (orderedIds: string[]) => void;
}

export const SubscriptionList: React.FC<SubscriptionListProps> = ({
  subscriptions,
  loading,
  selectedId,
  onSelect,
  onDelete,
  onReorder,
}) => {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);

  if (loading) return null;

  if (subscriptions.length === 0) {
    return (
      <EmptyState
        icon={<Rss className="h-8 w-8" />}
        title="还没有订阅"
        description="切到「探索」标签，浏览股市相关 RSSHub 路由并订阅感兴趣的源。"
      />
    );
  }

  const handleDrop = (targetIndex: number) => {
    if (dragIndex === null || dragIndex === targetIndex) {
      setDragIndex(null);
      setOverIndex(null);
      return;
    }
    const next = [...subscriptions];
    const [moved] = next.splice(dragIndex, 1);
    next.splice(targetIndex, 0, moved);
    onReorder(next.map((s) => s.id));
    setDragIndex(null);
    setOverIndex(null);
  };

  return (
    <div className="space-y-2">
      {subscriptions.map((sub, idx) => {
        const active = sub.id === selectedId;
        const isOver = overIndex === idx && dragIndex !== null && dragIndex !== idx;
        return (
          <div
            key={sub.id}
            draggable
            onDragStart={() => setDragIndex(idx)}
            onDragOver={(e) => { e.preventDefault(); setOverIndex(idx); }}
            onDrop={(e) => { e.preventDefault(); handleDrop(idx); }}
            onDragEnd={() => { setDragIndex(null); setOverIndex(null); }}
            className={cn(
              'group flex items-center gap-2 rounded-xl border bg-card p-2 transition',
              active ? 'border-cyan/60 shadow-soft-card' : 'border-border hover:border-cyan/40',
              isOver && 'border-cyan ring-2 ring-cyan/20',
              dragIndex === idx && 'opacity-50',
            )}
          >
            <span
              className="cursor-grab text-muted-text hover:text-secondary-text active:cursor-grabbing"
              aria-label="拖拽排序"
            >
              <GripVertical className="h-4 w-4" />
            </span>
            <button
              type="button"
              onClick={() => onSelect(sub)}
              className="flex min-w-0 flex-1 items-center gap-2 text-left"
            >
              <span
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg',
                  active ? 'bg-cyan/15 text-cyan' : 'bg-muted text-muted-text',
                )}
              >
                <Rss className="h-3.5 w-3.5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">{sub.title}</span>
                <code className="block truncate text-[10px] text-muted-text">{sub.routePath}</code>
              </span>
            </button>
            <button
              type="button"
              onClick={() => onDelete(sub)}
              className="shrink-0 rounded-md p-1.5 text-muted-text opacity-0 transition hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
              aria-label="删除订阅"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
};

export default SubscriptionList;
