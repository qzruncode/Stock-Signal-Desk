import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '../../utils/cn';
import { SelectPrimitive } from '../ui/select';

export interface CompactSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface CompactSelectOptionAction {
  ariaLabel: string;
  title?: string;
  onClick: () => void;
  children: ReactNode;
}

export type CompactSelectMenuBehavior = 'overlay' | 'flow';

export type CompactSelectDensity = 'compact' | 'regular';

export interface CompactSelectProps {
  id?: string;
  value: string;
  options: CompactSelectOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  triggerClassName?: string;
  disabled?: boolean;
  renderOptionAction?: (option: CompactSelectOption) => CompactSelectOptionAction | undefined;
  menuBehavior?: CompactSelectMenuBehavior;
  density?: CompactSelectDensity;
}

const EMPTY_OPTION_VALUE = '__dsa_empty_option__';

function toRadixValue(value: string) {
  return value === '' ? EMPTY_OPTION_VALUE : value;
}

function fromRadixValue(value: string) {
  return value === EMPTY_OPTION_VALUE ? '' : value;
}

/** Radix-backed select used by both compact settings controls and regular forms. */
export function CompactSelect({
  id,
  value,
  options,
  onChange,
  ariaLabel,
  className,
  triggerClassName,
  disabled = false,
  renderOptionAction,
  menuBehavior = 'overlay',
  density = 'compact',
}: CompactSelectProps) {
  const [open, setOpen] = useState(false);
  const selectionHandledRef = useRef(false);
  const triggerWrapperRef = useRef<HTMLDivElement>(null);
  const isRegular = density === 'regular';

  useEffect(() => {
    if (!open) return undefined;

    // Keep the legacy form behavior: changing another control also dismisses the open menu.
    const closeOnExternalChange = (event: Event) => {
      const target = event.target;
      if (target instanceof Node && !triggerWrapperRef.current?.contains(target)) {
        setOpen(false);
      }
    };
    document.addEventListener('change', closeOnExternalChange, true);
    return () => document.removeEventListener('change', closeOnExternalChange, true);
  }, [open]);

  const content = (
    <SelectPrimitive.Content
      position={menuBehavior === 'flow' ? 'item-aligned' : 'popper'}
      sideOffset={6}
      align="start"
      className={cn(
        'z-[70] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md',
        'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
        menuBehavior === 'flow'
          ? 'relative mt-1.5 w-full'
          : 'min-w-[var(--radix-select-trigger-width)]',
      )}
    >
      <SelectPrimitive.Viewport className="max-h-64 w-full overflow-y-auto">
        {options.map((option) => {
          const optionAction = option.disabled ? undefined : renderOptionAction?.(option);
          return (
            <SelectPrimitive.Item
              key={option.value || EMPTY_OPTION_VALUE}
              value={toRadixValue(option.value)}
              disabled={option.disabled}
              textValue={option.label}
              className={cn(
                'group relative flex w-full items-center justify-between gap-2 rounded-sm outline-none transition',
                isRegular
                  ? 'min-h-9 px-3 py-1.5 text-sm'
                  : 'min-h-7 px-2 py-1 text-[11px]',
                'text-secondary-text data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground',
                'data-[state=checked]:bg-accent data-[state=checked]:font-medium data-[state=checked]:text-accent-foreground',
                'data-[disabled]:cursor-not-allowed data-[disabled]:text-muted-foreground/45',
              )}
              onPointerDown={(event) => {
                if ((event.target as HTMLElement).closest('[data-select-option-action]')) {
                  event.preventDefault();
                  event.stopPropagation();
                }
              }}
              onClick={(event) => {
                event.preventDefault();
                if (selectionHandledRef.current) {
                  selectionHandledRef.current = false;
                  setOpen(false);
                  return;
                }
                if (!option.disabled) {
                  onChange(option.value);
                  setOpen(false);
                }
              }}
            >
              <SelectPrimitive.ItemText className="truncate">{option.label}</SelectPrimitive.ItemText>
              {optionAction ? (
                <button
                  type="button"
                  data-select-option-action
                  aria-label={optionAction.ariaLabel}
                  title={optionAction.title}
                    onPointerDown={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                    }}
                    onPointerUp={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                    }}
                    onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    optionAction.onClick();
                    setOpen(false);
                  }}
                  className="pointer-events-none inline-flex size-5 shrink-0 items-center justify-center rounded text-cyan opacity-0 outline-none transition hover:bg-cyan/15 focus-visible:bg-cyan/15 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100"
                >
                  {optionAction.children}
                </button>
              ) : null}
            </SelectPrimitive.Item>
          );
        })}
      </SelectPrimitive.Viewport>
    </SelectPrimitive.Content>
  );

  return (
    <SelectPrimitive.Root
      value={toRadixValue(value)}
      onValueChange={(nextValue) => {
        selectionHandledRef.current = true;
        onChange(fromRadixValue(nextValue));
        setOpen(false);
      }}
      open={open}
      onOpenChange={setOpen}
      disabled={disabled}
    >
      <div ref={triggerWrapperRef} className={cn('relative min-w-0', className)}>
        <SelectPrimitive.Trigger
          id={id}
          aria-label={ariaLabel}
          data-slot="select-trigger"
          className={cn(
            isRegular
              ? 'flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm transition-colors'
            : 'flex h-8 w-full items-center justify-between gap-1.5 rounded-md border border-input bg-background px-2 text-[11px] text-foreground shadow-sm transition-colors',
            'group hover:border-primary/40 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1',
            'data-[state=open]:border-primary/50 data-[state=open]:bg-accent/30',
            'disabled:cursor-not-allowed disabled:opacity-60',
            triggerClassName,
          )}
        >
          <SelectPrimitive.Value placeholder="请选择" />
          <SelectPrimitive.Icon asChild>
            <ChevronDown className={cn(
              isRegular ? 'size-4' : 'size-3',
              'shrink-0 text-muted-foreground transition-transform',
              'group-data-[state=open]:rotate-180 group-data-[state=open]:text-cyan',
            )} />
          </SelectPrimitive.Icon>
        </SelectPrimitive.Trigger>
      </div>
      {menuBehavior === 'flow' ? content : <SelectPrimitive.Portal>{content}</SelectPrimitive.Portal>}
    </SelectPrimitive.Root>
  );
}

export default CompactSelect;
