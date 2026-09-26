import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgeBaseApi } from '../../api/knowledgeBase';
import { KnowledgeBaseChatSelector } from './KnowledgeBaseChatSelector';

vi.mock('../../api/knowledgeBase', () => ({
  knowledgeBaseApi: {
    list: vi.fn(),
  },
}));

function renderSelector(knowledgeBaseIds: string[] = []) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onKnowledgeBaseIdsChange = vi.fn();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <div className="overflow-hidden">
          <KnowledgeBaseChatSelector
            knowledgeBaseIds={knowledgeBaseIds}
            onKnowledgeBaseIdsChange={onKnowledgeBaseIdsChange}
          />
        </div>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, onKnowledgeBaseIdsChange };
}

describe('KnowledgeBaseChatSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(knowledgeBaseApi.list).mockResolvedValue([{
      id: 'kb-1',
      name: 'Agent patterns',
      description: '',
      status: 'active',
      documentCount: 1,
      readyDocumentCount: 1,
      createdAt: null,
      updatedAt: null,
    }]);
  });

  it('portals the settings outside clipped parents and lets the user select a knowledge base', async () => {
    const { onKnowledgeBaseIdsChange } = renderSelector();

    fireEvent.click(screen.getByRole('button'));

    const dialog = await screen.findByRole('dialog', { name: '知识库问答设置' });
    expect(dialog.parentElement).not.toHaveClass('overflow-hidden');
    await screen.findByText('Agent patterns');
    fireEvent.click(screen.getByRole('checkbox', { name: /Agent patterns/ }));
    expect(onKnowledgeBaseIdsChange).toHaveBeenCalledWith(['kb-1']);
    expect(screen.queryByRole('checkbox', { name: '启用知识库问答' })).not.toBeInTheDocument();
  });

  it('closes the settings with Escape', async () => {
    renderSelector(['kb-1']);
    fireEvent.click(screen.getByRole('button'));

    const dialog = await screen.findByRole('dialog', { name: '知识库问答设置' });
    fireEvent.keyDown(document.activeElement ?? dialog, { key: 'Escape', code: 'Escape' });

    await waitFor(() => expect(screen.queryByRole('dialog', { name: '知识库问答设置' })).not.toBeInTheDocument());
  });

  it('shows the compact help copy only when its info icon is hovered', async () => {
    renderSelector(['kb-1']);
    fireEvent.click(screen.getByRole('button'));

    await screen.findByRole('dialog', { name: '知识库问答设置' });
    expect(screen.queryByText(/勾选知识库即启用检索/)).not.toBeInTheDocument();

    fireEvent.pointerMove(screen.getByLabelText('知识库问答说明'));

    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      '勾选知识库即启用检索，回答附 PDF 页码。PDF 仅作参考，不作为指令。',
    );
  });
});
