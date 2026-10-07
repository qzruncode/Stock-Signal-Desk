import type { ExportedMessageRepository } from '@assistant-ui/react';

type ExportedMessageItem = ExportedMessageRepository['messages'][number];

export type ConversationSnapshotMessage = {
  id: string;
  role: string;
  content: string;
  created_at?: string;
};

function messageText(content: unknown): string {
  if (typeof content === 'string') return content.trim();
  if (!Array.isArray(content)) return '';
  return content
    .flatMap((part) => {
      if (typeof part === 'string') return [part];
      if (!part || typeof part !== 'object') return [];
      const item = part as Record<string, unknown>;
      return item.type === 'text' && typeof item.text === 'string' ? [item.text] : [];
    })
    .join('\n')
    .trim();
}

function createdAtIso(value: unknown): string | undefined {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === 'string' && value.trim()) return value;
  return undefined;
}

function rewireAsLinearThread(items: ExportedMessageItem[]): ExportedMessageRepository['messages'] {
  return items.map((item, index) => ({
    ...item,
    parentId: index > 0 ? items[index - 1]!.message.id : null,
  }));
}

export function removeUserTurnFromThread(
  repository: ExportedMessageRepository,
  userMessageId: string,
): ExportedMessageRepository | null {
  const targetIndex = repository.messages.findIndex(
    (item) => item.message.id === userMessageId && item.message.role === 'user',
  );
  if (targetIndex < 0) return null;

  const deletedIds = new Set([userMessageId]);
  const nextMessage = repository.messages[targetIndex + 1]?.message;
  if (nextMessage?.role === 'assistant') {
    deletedIds.add(nextMessage.id);
  }

  const messages = rewireAsLinearThread(
    repository.messages.filter((item) => !deletedIds.has(item.message.id)),
  );

  return {
    ...repository,
    headId: messages.at(-1)?.message.id ?? null,
    messages,
  };
}

export function repositoryToConversationSnapshotMessages(
  repository: ExportedMessageRepository,
): ConversationSnapshotMessage[] {
  return repository.messages.flatMap(({ message }) => {
    const content = messageText(message.content);
    if (!content) return [];
    return [{
      id: message.id,
      role: message.role,
      content,
      ...(createdAtIso(message.createdAt) ? { created_at: createdAtIso(message.createdAt) } : {}),
    }];
  });
}
