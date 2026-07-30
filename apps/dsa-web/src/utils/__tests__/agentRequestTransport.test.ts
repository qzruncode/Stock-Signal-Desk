import { describe, expect, it } from 'vitest';
import type { ExportedMessageRepository } from '@assistant-ui/core';
import { currentUserRequest } from '../agentRequestTransport';

describe('currentUserRequest', () => {
  it('sends only the latest user turn and keeps its server-history parent', () => {
    const repository = {
      messages: [
        {
          parentId: null,
          message: {
            id: 'u1',
            role: 'user',
            content: [{ type: 'text', text: '旧问题' }],
            attachments: [],
          },
        },
        {
          parentId: 'u1',
          message: {
            id: 'a1',
            role: 'assistant',
            content: [
              { type: 'reasoning', text: '很长的实时过程' },
              {
                type: 'tool-call',
                toolCallId: 'tool-1',
                toolName: 'large_tool',
                args: { payload: '不会回传' },
                argsText: '{}',
                result: { items: Array.from({ length: 500 }, (_, index) => index) },
              },
              { type: 'text', text: '旧回答' },
            ],
          },
        },
        {
          parentId: 'a1',
          message: {
            id: 'u2',
            role: 'user',
            content: [{ type: 'text', text: '继续分析' }],
            attachments: [{
              id: 'attachment-1',
              type: 'document',
              name: 'note.txt',
              status: { type: 'complete' },
              content: [{
                type: 'file',
                data: 'data:text/plain;base64,Zm9v',
                mimeType: 'text/plain',
              }],
            }],
          },
        },
      ],
    } as unknown as ExportedMessageRepository;

    expect(currentUserRequest(repository)).toEqual({
      historyParentId: 'a1',
      messages: [{
        id: 'u2',
        role: 'user',
        content: [
          { type: 'text', text: '继续分析' },
          {
            type: 'file',
            data: 'data:text/plain;base64,Zm9v',
            mediaType: 'text/plain',
          },
        ],
      }],
    });
  });
});
