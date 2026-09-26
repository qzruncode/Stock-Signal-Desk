import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ExternalLinkIcon, InfoIcon, Loader2Icon, PlusIcon } from 'lucide-react';
import { Popover as PopoverPrimitive } from 'radix-ui';
import { knowledgeBaseApi } from '../../api/knowledgeBase';
import { Tooltip } from '../common/Tooltip';
import { cn } from '../../utils/cn';

type Props = {
  knowledgeBaseIds: string[];
  disabled?: boolean;
  onKnowledgeBaseIdsChange: (ids: string[]) => void;
};

export function KnowledgeBaseChatSelector({
  knowledgeBaseIds,
  disabled = false,
  onKnowledgeBaseIdsChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const bases = useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: knowledgeBaseApi.list,
    staleTime: 15_000,
  });
  const available = (bases.data ?? []).filter((item) => item.status === 'active');
  const selectedCount = knowledgeBaseIds.filter((id) => available.some((item) => item.id === id)).length;

  const toggleKnowledgeBase = (id: string, checked: boolean) => {
    const next = checked
      ? [...new Set([...knowledgeBaseIds, id])]
      : knowledgeBaseIds.filter((item) => item !== id);
    onKnowledgeBaseIdsChange(next.slice(0, 8));
  };

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={selectedCount
            ? `已选择 ${selectedCount} 个知识库，点击修改检索范围`
            : '选择知识库检索范围'}
          className={cn(
            'relative inline-flex size-8 items-center justify-center rounded-lg transition',
            selectedCount > 0
              ? 'bg-primary/8 text-primary hover:bg-primary/12'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            disabled && 'cursor-not-allowed opacity-50',
          )}
          title="选择知识库检索范围"
        >
          <PlusIcon className="size-4" />
          {selectedCount > 0 ? (
            <span className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-primary ring-2 ring-card" aria-hidden="true" />
          ) : null}
        </button>
      </PopoverPrimitive.Trigger>

      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          role="dialog"
          aria-label="知识库问答设置"
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={8}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            titleRef.current?.focus();
          }}
          className="z-[60] w-[min(22rem,calc(100vw-1rem))] max-h-[min(32rem,calc(100dvh-1rem))] overflow-y-auto overscroll-contain rounded-xl border border-border bg-card p-3 shadow-xl"
        >
          <div className="flex items-center gap-1.5">
            <h2 ref={titleRef} tabIndex={-1} className="text-sm font-semibold text-foreground">知识库问答</h2>
            <Tooltip
              focusable
              ariaLabel="知识库问答说明"
              content="勾选知识库即启用检索，回答附 PDF 页码。PDF 仅作参考，不作为指令。"
              contentClassName="min-w-0 max-w-[18rem] whitespace-normal"
            >
              <InfoIcon className="size-3.5 cursor-help text-muted-foreground transition-colors hover:text-primary" aria-hidden="true" />
            </Tooltip>
          </div>

          <div className="mt-3 border-t border-border/70 pt-2">
            <div className="mb-1 flex items-center justify-between">
              <p className="text-[11px] font-medium text-muted-foreground">检索范围（最多 8 个）</p>
              <Link
                to="/knowledge"
                onClick={() => setOpen(false)}
                className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                管理知识库 <ExternalLinkIcon className="size-3" />
              </Link>
            </div>
            {bases.isLoading ? (
              <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
                <Loader2Icon className="size-3.5 animate-spin" /> 正在加载知识库
              </div>
            ) : bases.isError ? (
              <div className="rounded-lg bg-red-50 px-2.5 py-2 text-xs text-red-700">
                知识库列表加载失败。刷新后重试，或前往管理页检查服务状态。
              </div>
            ) : available.length === 0 ? (
              <div className="rounded-lg bg-muted/60 px-2.5 py-2 text-xs text-muted-foreground">
                暂无可用知识库
              </div>
            ) : (
              <div className="max-h-56 space-y-1 overflow-y-auto">
                {available.map((base) => {
                  const checked = knowledgeBaseIds.includes(base.id);
                  const atLimit = knowledgeBaseIds.length >= 8 && !checked;
                  return (
                    <label
                      key={base.id}
                      className={cn(
                        'flex cursor-pointer items-start gap-2 rounded-lg px-2 py-2 transition hover:bg-muted/60',
                        atLimit && 'cursor-not-allowed opacity-50',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled || atLimit}
                        onChange={(event) => toggleKnowledgeBase(base.id, event.target.checked)}
                        className="mt-0.5 size-3.5 shrink-0 accent-primary"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium text-foreground">{base.name}</span>
                        <span className="mt-0.5 block text-[10px] text-muted-foreground">
                          {base.readyDocumentCount}/{base.documentCount} 份文档可检索
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
