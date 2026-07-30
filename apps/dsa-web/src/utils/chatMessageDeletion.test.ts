import { describe, expect, it } from 'vitest';
import type { ExportedMessageRepository } from '@assistant-ui/core';

import {
  removeUserTurnFromThread,
  repositoryToConversationSnapshotMessages,
} from './chatMessageDeletion';

function message(id: string, role: 'user' | 'assistant', text: string) {
  return {
    id,
    role,
    content: [{ type: 'text', text }],
    createdAt: new Date(`2026-07-28T00:00:0${id.slice(-1)}Z`),
    metadata: {},
    status: { type: 'complete', reason: 'stop' },
  };
}

function repository(): ExportedMessageRepository {
  const messages = [
    message('u1', 'user', '问题一'),
    message('a1', 'assistant', '回答一'),
    message('u2', 'user', '问题二'),
    message('a2', 'assistant', '回答二'),
    message('u3', 'user', '问题三'),
  ];
  return {
    headId: 'u3',
    messages: messages.map((item, index) => ({
      message: item,
      parentId: index > 0 ? messages[index - 1]!.id : null,
    })) as unknown as ExportedMessageRepository['messages'],
  };
}

describe('removeUserTurnFromThread', () => {
  it('removes the selected user message and the following assistant answer', () => {
    const next = removeUserTurnFromThread(repository(), 'u2');

    expect(next?.messages.map((item) => item.message.id)).toEqual(['u1', 'a1', 'u3']);
    expect(next?.headId).toBe('u3');
    expect(next?.messages.map((item) => item.parentId)).toEqual([null, 'u1', 'a1']);
  });

  it('exports the remaining visible transcript for backend snapshot sync', () => {
    const next = removeUserTurnFromThread(repository(), 'u2');

    expect(repositoryToConversationSnapshotMessages(next!)).toEqual([
      expect.objectContaining({ id: 'u1', role: 'user', content: '问题一' }),
      expect.objectContaining({ id: 'a1', role: 'assistant', content: '回答一' }),
      expect.objectContaining({ id: 'u3', role: 'user', content: '问题三' }),
    ]);
  });
});
