import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppToastProvider } from '../Toast';
import { useToast } from '../ToastContext';

function ToastTrigger() {
  const { toast } = useToast();

  return (
    <button
      type="button"
      onClick={() => toast({ title: '保存成功', description: '配置已重新加载。', variant: 'success' })}
    >
      显示提示
    </button>
  );
}

describe('AppToastProvider', () => {
  it('renders transient feedback outside the page flow', () => {
    render(
      <AppToastProvider>
        <ToastTrigger />
      </AppToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '显示提示' }));

    expect(screen.getByText('保存成功')).toBeInTheDocument();
    expect(document.querySelector('[data-slot="toast-description"]')).toHaveTextContent('配置已重新加载。');
    expect(document.querySelector('[data-slot="toast-description"]')).toHaveClass('truncate');
  });
});
