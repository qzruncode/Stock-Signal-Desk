import { Check, ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { cn } from '../../utils/cn';

interface CompactSelectOption {
  value: string;
  label: string;
  count?: number;
  showSelectedIndicator?: boolean;
  manageActions?: boolean;
}

export function StockListCompactSelect({
  value,
  options,
  onChange,
  ariaLabel,
  className,
  renderOptionActions,
}: {
  value: string;
  options: CompactSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  renderOptionActions?: (option: CompactSelectOption, close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={cn('relative shrink-0', className)}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          'input-surface flex h-8 w-full min-w-[6.5rem] items-center justify-between gap-1.5 rounded-md border px-2 text-[11px] text-foreground transition',
          'hover:border-cyan/40 hover:bg-cyan/5 focus:outline-none focus:ring-2 focus:ring-cyan/15',
          open && 'border-cyan/50 bg-cyan/5',
        )}
      >
        <span className="truncate">{selected?.label ?? '请选择'}</span>
        <ChevronDown className={cn('size-3 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180 text-cyan')} />
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label={ariaLabel}
          className="absolute right-0 top-[calc(100%+0.4rem)] z-50 min-w-full overflow-hidden rounded-xl border border-border/70 bg-white/95 p-1 shadow-[0_16px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl"
        >
          {options.map((option) => {
            const selectedOption = option.value === value;
            return (
              <div
                key={option.value}
                role="option"
                aria-selected={selectedOption}
                className={cn(
                  'group relative flex w-full items-center gap-1 rounded-md px-1 transition',
                  selectedOption ? 'bg-cyan/10 text-cyan' : 'text-secondary-text',
                )}
              >
                <button
                  type="button"
                  onClick={() => {
                    onChange(option.value);
                    setOpen(false);
                  }}
                  className={cn(
                    'flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-1.5 py-1.5 text-left text-[11px] transition',
                    selectedOption
                      ? 'font-medium text-cyan'
                      : 'hover:bg-elevated/70 hover:text-foreground',
                  )}
                >
                  <span className="truncate">{option.label}</span>
                  <span className={cn(
                    'flex shrink-0 items-center gap-1.5',
                    option.count !== undefined && 'ml-auto min-w-10 justify-end text-right',
                  )}>
                    {option.count !== undefined ? <span className="text-[10px] text-muted-foreground">{option.count}</span> : null}
                    {selectedOption && option.showSelectedIndicator !== false ? <Check className="size-3.5" /> : null}
                  </span>
                </button>
                {option.manageActions ? (
                  <div className="pointer-events-none absolute right-10 top-1/2 z-10 flex -translate-y-1/2 items-center gap-0 p-0 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
                    {renderOptionActions?.(option, () => setOpen(false))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
