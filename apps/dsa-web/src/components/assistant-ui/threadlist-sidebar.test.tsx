import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ThreadListSidebar } from './threadlist-sidebar';

describe('ThreadListSidebar', () => {
  it('exposes settings at the bottom without a separate run shortcut', () => {
    render(
      <MemoryRouter>
        <ThreadListSidebar
          conversations={[]}
          selectedConversationId={null}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn()}
          onDelete={vi.fn()}
          onClearAll={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: 'AI 助手设置' })).toHaveAttribute('href', '/setting');
    expect(screen.queryByRole('link', { name: '查看运行记录' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '管理自选股' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '工作流编排' })).not.toBeInTheDocument();
  });

  it('marks a selected conversation as busy while its detail is loading', () => {
    render(
      <MemoryRouter>
        <ThreadListSidebar
          conversations={[{
            id: 'conversation-1',
            title: '市场主线',
            titleSource: 'manual',
            previewText: '',
            createdAt: '2026-07-30T00:00:00Z',
            updatedAt: '2026-07-30T00:00:00Z',
          }]}
          selectedConversationId="conversation-1"
          loadingConversationId="conversation-1"
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn()}
          onDelete={vi.fn()}
          onClearAll={vi.fn()}
        />
      </MemoryRouter>,
    );

    const conversationButton = screen.getByRole('button', { name: '市场主线' });
    expect(conversationButton).toHaveAttribute('aria-current', 'true');
    expect(conversationButton).toHaveAttribute('aria-busy', 'true');
  });

  it('provides a direct clear-all-history action', () => {
    const onClearAll = vi.fn();
    render(
      <MemoryRouter>
        <ThreadListSidebar
          conversations={[{
            id: 'conversation-1',
            title: '市场主线',
            titleSource: 'manual',
            previewText: '',
            createdAt: '2026-07-30T00:00:00Z',
            updatedAt: '2026-07-30T00:00:00Z',
          }]}
          selectedConversationId={null}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn()}
          onDelete={vi.fn()}
          onClearAll={onClearAll}
        />
      </MemoryRouter>,
    );

    screen.getByRole('button', { name: '清除全部会话历史' }).click();
    expect(onClearAll).toHaveBeenCalledOnce();
  });
});
