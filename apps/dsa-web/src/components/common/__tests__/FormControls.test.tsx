import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CompactSelect } from '../CompactSelect';
import { FormCheckbox, FormNumberInput, FormSelect } from '../FormControls';

describe('FormControls', () => {
  it('renders the shared select surface instead of a native select', () => {
    const onChange = vi.fn();
    const { container } = render(
      <FormSelect
        label="比较"
        value="gt"
        onChange={onChange}
        options={[
          { value: 'gt', label: '>' },
          { value: 'gte', label: '≥' },
        ]}
      />,
    );

    expect(container.querySelector('select')).toBeNull();
    fireEvent.click(screen.getByRole('combobox', { name: '比较' }));
    fireEvent.click(screen.getByRole('option', { name: '≥' }));
    expect(onChange).toHaveBeenCalledWith('gte');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('keeps number values and labels aligned through the shared field API', () => {
    const onChange = vi.fn();
    render(
      <FormNumberInput
        label="阈值"
        value={2.8}
        onChange={onChange}
        layout="inline"
      />,
    );

    const input = screen.getByRole('spinbutton', { name: '阈值' });
    expect(input).toHaveValue(2.8);
    fireEvent.change(input, { target: { value: '3.1' } });
    expect(onChange).toHaveBeenCalledWith(3.1);
  });

  it('closes a direct compact select when its empty value changes', () => {
    const onChange = vi.fn();
    render(
      <CompactSelect
        ariaLabel="已有分组"
        value=""
        onChange={onChange}
        options={[
          { value: '', label: '不选择已有分组' },
          { value: '7', label: '已有分组（1）' },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole('combobox', { name: '已有分组' }));
    fireEvent.click(screen.getByRole('option', { name: '已有分组（1）' }));
    expect(onChange).toHaveBeenCalledWith('7');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('keeps an option action clickable without selecting the option', () => {
    const onChange = vi.fn();
    const onAction = vi.fn();
    render(
      <CompactSelect
        ariaLabel="指标"
        value="atr"
        onChange={onChange}
        options={[
          { value: 'atr', label: 'ATR 相对波动率' },
          { value: 'profit', label: '归母净利润' },
        ]}
        renderOptionAction={(option) => ({
          ariaLabel: `设置 ${option.label}`,
          onClick: onAction,
          children: '⚙',
        })}
      />,
    );

    fireEvent.click(screen.getByRole('combobox', { name: '指标' }));
    const action = screen.getByRole('button', { name: '设置 归母净利润' });
    fireEvent.pointerDown(action, { pointerType: 'mouse' });
    fireEvent.pointerUp(action, { pointerType: 'mouse' });
    fireEvent.click(action);

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('provides an accessible custom checkbox for schema-driven forms', () => {
    const onChange = vi.fn();
    render(
      <FormCheckbox
        label="启用"
        checked={false}
        onChange={onChange}
      />,
    );

    const checkbox = screen.getByRole('checkbox', { name: '启用' });
    expect(checkbox).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
