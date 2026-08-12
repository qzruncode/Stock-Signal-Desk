import { render, screen, waitFor } from '@testing-library/react';
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
          onBatchDelete={vi.fn()}
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
          onBatchDelete={vi.fn()}
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
          onBatchDelete={vi.fn()}
          onClearAll={onClearAll}
        />
      </MemoryRouter>,
    );

    screen.getByRole('button', { name: '清除全部会话历史' }).click();
    expect(onClearAll).toHaveBeenCalledOnce();
  });

  it('supports selecting multiple conversations and deleting the selection', async () => {
    const onBatchDelete = vi.fn();
    render(
      <MemoryRouter>
        <ThreadListSidebar
          conversations={[
            {
              id: 'conversation-1',
              title: '市场主线',
              titleSource: 'manual',
              previewText: '',
              createdAt: '2026-07-30T00:00:00Z',
              updatedAt: '2026-07-30T00:00:00Z',
            },
            {
              id: 'conversation-2',
              title: '机器人产业链',
              titleSource: 'manual',
              previewText: '',
              createdAt: '2026-07-29T00:00:00Z',
              updatedAt: '2026-07-29T00:00:00Z',
            },
          ]}
          selectedConversationId={null}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn()}
          onDelete={vi.fn()}
          onBatchDelete={onBatchDelete}
          onClearAll={vi.fn()}
        />
      </MemoryRouter>,
    );

    screen.getByRole('button', { name: '批量管理' }).click();
    (await screen.findByRole('button', { name: '选择对话：市场主线' })).click();
    (await screen.findByRole('button', { name: '删除选中（1）' })).click();

    await waitFor(() => {
      expect(onBatchDelete).toHaveBeenCalledWith(['conversation-1']);
      expect(screen.queryByRole('button', { name: '退出批量管理' })).not.toBeInTheDocument();
    });
  });

  it('uses the same delete button to clear all when nothing is selected', async () => {
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
          onBatchDelete={vi.fn()}
          onClearAll={onClearAll}
        />
      </MemoryRouter>,
    );

    screen.getByRole('button', { name: '批量管理' }).click();
    await screen.findByText('未选择，删除按钮将清除全部');
    screen.getByRole('button', { name: '清除全部会话历史' }).click();

    expect(onClearAll).toHaveBeenCalledOnce();
  });
});
