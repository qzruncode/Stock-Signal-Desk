import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { agentApi, type ChatConversationDetail } from '../../api/agent';
import { WorkspaceQueryProvider } from '../../components/layout/WorkspaceQueryProvider';
import { useChatController } from '../useChatController';

vi.mock('@assistant-ui/react-data-stream', () => ({ useDataStreamRuntime: vi.fn(() => ({})) }));
vi.mock('../../api/agent', () => ({ agentApi: {
  listConversations: vi.fn(), getConversation: vi.fn(), cancelConversationRun: vi.fn(), createConversation: vi.fn(),
} }));

const detail = (id: string): ChatConversationDetail => ({
  id, title: id, titleSource: 'manual', createdAt: '2026-09-06', updatedAt: '2026-09-06',
  messages: [], isGenerating: false,
});
const wrapper = ({ children }: PropsWithChildren) => <WorkspaceQueryProvider>{children}</WorkspaceQueryProvider>;

describe('Conversation query and stream boundaries', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem('dsa.knowledge-chat-preferences.v2');
    localStorage.removeItem('dsa.knowledge-chat-preferences.v1');
    vi.mocked(agentApi.listConversations).mockResolvedValue({ items: [detail('a'), detail('b')], total: 2, page: 1, limit: 20 });
    vi.mocked(agentApi.getConversation).mockImplementation(async id => detail(id));
    vi.mocked(agentApi.cancelConversationRun).mockResolvedValue(true);
    vi.mocked(agentApi.createConversation).mockResolvedValue(detail('new'));
  });

  it('keeps the selected knowledge bases when the initial conversation is created', async () => {
    localStorage.setItem('dsa.knowledge-chat-preferences.v2', JSON.stringify({
      __new__: { knowledgeBaseIds: ['kb-1'] },
    }));
    vi.mocked(agentApi.listConversations).mockResolvedValue({ items: [], total: 0, page: 1, limit: 20 });
    vi.mocked(agentApi.createConversation).mockResolvedValue(detail('fresh'));

    const { result } = renderHook(useChatController, { wrapper });

    await waitFor(() => {
      expect(result.current.selectedConversationId).toBe('fresh');
      expect(result.current.knowledgeBaseIds).toEqual(['kb-1']);
    });
    expect(JSON.parse(localStorage.getItem('dsa.knowledge-chat-preferences.v2') ?? '{}').fresh.knowledgeBaseIds).toEqual(['kb-1']);
  });

  it('does not replace the selected conversation with a slower previous response', async () => {
    let resolvePrevious!: (value: ChatConversationDetail) => void;
    const previous = new Promise<ChatConversationDetail>(resolve => { resolvePrevious = resolve; });
    vi.mocked(agentApi.getConversation).mockImplementation(id => id === 'a' ? previous : Promise.resolve(detail(id)));
    const { result } = renderHook(useChatController, { wrapper });
    await waitFor(() => expect(result.current.selectedConversationId).toBe('a'));
    act(() => result.current.handleSelectConversation('b'));
    await waitFor(() => expect(result.current.selectedConversationDetail?.id).toBe('b'));
    await act(async () => { resolvePrevious(detail('a')); await previous; });
    expect(result.current.selectedConversationDetail?.id).toBe('b');
  });

  it('deduplicates in-flight requests with the native query cache', async () => {
    let resolveDetail!: (value: ChatConversationDetail) => void;
    const pending = new Promise<ChatConversationDetail>(resolve => { resolveDetail = resolve; });
    vi.mocked(agentApi.getConversation).mockReturnValue(pending);
    const { result } = renderHook(useChatController, { wrapper });
    await waitFor(() => expect(agentApi.getConversation).toHaveBeenCalledTimes(1));
    act(() => result.current.handleRetryConversation());
    expect(agentApi.getConversation).toHaveBeenCalledTimes(1);
    await act(async () => { resolveDetail(detail('a')); await pending; });
    await waitFor(() => expect(result.current.selectedConversationDetail?.id).toBe('a'));
  });

  it('cancels through the backend and reloads the durable snapshot', async () => {
    const { result } = renderHook(useChatController, { wrapper });
    await waitFor(() => expect(result.current.selectedConversationDetail?.id).toBe('a'));
    act(() => result.current.handleUserCancelRun());
    await waitFor(() => expect(agentApi.cancelConversationRun).toHaveBeenCalledWith('a'));
    await waitFor(() => expect(agentApi.getConversation).toHaveBeenCalledTimes(2));
  });
});
