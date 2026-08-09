import React from 'react';
import { FormSelect } from './FormControls';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  label?: string;
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  searchable?: boolean;
  searchPlaceholder?: string;
  emptyText?: string;
}

/**
 * Backwards-compatible regular select API backed by the shared in-app select.
 */
export const Select: React.FC<SelectProps> = (props) => {
  const {
    id,
    value,
    onChange,
    options,
    label,
    ariaLabel,
    placeholder,
    disabled,
    className,
  } = props;

  return (
    <FormSelect
      id={id}
      value={value}
      onChange={onChange}
      options={options}
      label={label}
      ariaLabel={ariaLabel}
      placeholder={placeholder}
      disabled={disabled}
      className={className}
      density="regular"
    />
  );
};
