import { useId } from 'react';
import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import { CompactSelect } from './CompactSelect';
import type {
  CompactSelectDensity,
  CompactSelectOption,
  CompactSelectOptionAction,
  CompactSelectMenuBehavior,
} from './CompactSelect';
import { Input } from './Input';
import { Checkbox } from '../ui/checkbox';

export type FormFieldLayout = 'inline' | 'stacked';

export interface FormFieldProps {
  label?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  layout?: FormFieldLayout;
  className?: string;
  labelClassName?: string;
  controlClassName?: string;
}

/** Shared label/control layout for compact settings forms and regular forms. */
export function FormField({
  label,
  htmlFor,
  children,
  hint,
  error,
  layout = 'stacked',
  className,
  labelClassName,
  controlClassName,
}: FormFieldProps) {
  const inline = layout === 'inline';
  const labelClasses = cn(
    inline
      ? 'shrink-0 text-[11px] font-medium leading-4 text-secondary-text'
      : 'text-sm font-medium text-foreground',
    labelClassName,
  );

  return (
    <div className={cn(inline ? 'flex items-center gap-2' : 'flex flex-col gap-1.5', className)}>
      {label ? (
        htmlFor ? (
          <label htmlFor={htmlFor} className={labelClasses}>
            {label}
          </label>
        ) : (
          <span className={labelClasses}>{label}</span>
        )
      ) : null}
      <div className={cn(inline ? 'min-w-0 flex-1' : 'min-w-0', controlClassName)}>
        {children}
        {error ? <p className="mt-1.5 text-xs text-danger" role="alert">{error}</p> : null}
        {!error && hint ? <p className="mt-1.5 text-xs text-secondary-text">{hint}</p> : null}
      </div>
    </div>
  );
}

export interface FormSelectProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: CompactSelectOption[];
  label?: ReactNode;
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  controlClassName?: string;
  labelClassName?: string;
  hint?: ReactNode;
  error?: ReactNode;
  layout?: FormFieldLayout;
  density?: CompactSelectDensity;
  renderOptionAction?: (option: CompactSelectOption) => CompactSelectOptionAction | undefined;
  menuBehavior?: CompactSelectMenuBehavior;
}

/** Project select control. It deliberately uses the in-app select surface, never a native <select>. */
export function FormSelect({
  id,
  value,
  onChange,
  options,
  label,
  ariaLabel,
  placeholder = '请选择',
  disabled = false,
  className,
  controlClassName,
  labelClassName,
  hint,
  error,
  layout = 'stacked',
  density = 'regular',
  renderOptionAction,
  menuBehavior,
}: FormSelectProps) {
  const generatedId = useId();
  const resolvedId = id ?? generatedId;
  const hasEmptyOption = options.some((option) => option.value === '');
  const resolvedOptions = placeholder && !hasEmptyOption
    ? [{ value: '', label: placeholder, disabled: true }, ...options]
    : options;
  const resolvedAriaLabel = ariaLabel ?? (typeof label === 'string' ? label : '选择');

  return (
    <FormField
      label={label}
      htmlFor={resolvedId}
      hint={hint}
      error={error}
      layout={layout}
      className={className}
      labelClassName={labelClassName}
      controlClassName={controlClassName}
    >
      <CompactSelect
        id={resolvedId}
        value={value}
        options={resolvedOptions}
        onChange={onChange}
        ariaLabel={resolvedAriaLabel}
        disabled={disabled}
        density={density}
        renderOptionAction={renderOptionAction}
        menuBehavior={menuBehavior}
      />
    </FormField>
  );
}

export interface FormNumberInputProps {
  id?: string;
  value: string | number | null | undefined;
  onChange: (value: number | null) => void;
  label?: ReactNode;
  ariaLabel?: string;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  controlClassName?: string;
  labelClassName?: string;
  hint?: ReactNode;
  error?: ReactNode;
  layout?: FormFieldLayout;
}

export interface FormCheckboxProps {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: ReactNode;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  labelClassName?: string;
  layout?: FormFieldLayout;
}

/** Accessible checkbox surface that follows the same label alignment as other form controls. */
export function FormCheckbox({
  id,
  checked,
  onChange,
  label,
  ariaLabel,
  disabled = false,
  className,
  labelClassName,
  layout = 'inline',
}: FormCheckboxProps) {
  const generatedId = useId();
  const resolvedId = id ?? generatedId;
  const resolvedAriaLabel = ariaLabel ?? (typeof label === 'string' ? label : undefined);

  return (
    <FormField
      label={label}
      htmlFor={resolvedId}
      layout={layout}
      className={className}
      labelClassName={labelClassName}
    >
      <Checkbox
        id={resolvedId}
        aria-label={resolvedAriaLabel}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(nextChecked) => onChange(nextChecked === true)}
      />
    </FormField>
  );
}

/** Number input with the shared project field shell and value conversion. */
export function FormNumberInput({
  id,
  value,
  onChange,
  label,
  ariaLabel,
  min,
  max,
  step,
  placeholder,
  disabled = false,
  className,
  controlClassName,
  labelClassName,
  hint,
  error,
  layout = 'stacked',
}: FormNumberInputProps) {
  const generatedId = useId();
  const resolvedId = id ?? generatedId;
  const resolvedAriaLabel = ariaLabel ?? (typeof label === 'string' ? label : undefined);

  return (
    <FormField
      label={label}
      htmlFor={resolvedId}
      hint={hint}
      error={error}
      layout={layout}
      className={className}
      labelClassName={labelClassName}
      controlClassName={controlClassName}
    >
      <Input
        id={resolvedId}
        aria-label={resolvedAriaLabel}
        type="number"
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        disabled={disabled}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
      />
    </FormField>
  );
}
