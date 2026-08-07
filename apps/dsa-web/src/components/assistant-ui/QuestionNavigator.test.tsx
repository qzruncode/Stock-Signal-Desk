import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useThread } from '@assistant-ui/react';
import {
  getChatQuestionDomId,
} from '../../utils/chatQuestionLocator';
import { QuestionNavigator } from './QuestionNavigator';

vi.mock('@assistant-ui/react', () => ({
  useThread: vi.fn(),
}));

describe('QuestionNavigator', () => {
  beforeEach(() => {
    vi.mocked(useThread).mockReturnValue([
      {
        id: 'user-1',
        role: 'user',
        content: [{ type: 'text', text: '第一个问题' }],
      },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: [{ type: 'text', text: '第一个回答' }],
      },
      {
        id: 'user-2',
        role: 'user',
        content: [{ type: 'text', text: '第二个问题\n请继续核验' }],
      },
    ] as never);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('lists every user question and jumps to the selected one', () => {
    const target = document.createElement('div');
    target.id = getChatQuestionDomId('user-1');
    target.tabIndex = -1;
    target.scrollIntoView = vi.fn();
    target.focus = vi.fn();
    document.body.append(target);

    render(<QuestionNavigator />);
    const navigatorButton = screen.getByRole('button', { name: '打开问题导航' });
    fireEvent.click(navigatorButton);

    expect(screen.getByRole('button', { name: '第 1 个问题：第一个问题' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '第 2 个问题：第二个问题 请继续核验' }))
      .toHaveAttribute('aria-current', 'true');

    fireEvent.click(screen.getByRole('button', { name: '第 1 个问题：第一个问题' }));

    expect(target.scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'center',
    });
    expect(target).toHaveAttribute('data-chat-question-located', 'true');
    expect(screen.queryByRole('button', { name: '第 1 个问题：第一个问题' }))
      .not.toBeInTheDocument();
  });
});
