import type { ExportedMessageRepository, ThreadUserMessagePart } from '@assistant-ui/react';

type AgentRequestPart =
  | { type: 'text'; text: string }
  | { type: 'file'; data: string; mediaType: string };

export type CurrentUserRequest = {
  messages: Array<{
    id: string;
    role: 'user';
    content: AgentRequestPart[];
  }>;
  historyParentId: string | null;
};

const imageMediaType = (value: string): string => {
  if (value.startsWith('data:')) {
    return value.match(/^data:([^;,]+)/)?.[1] || 'image/png';
  }
  const extension = value.split(/[?#]/)[0]?.split('.').pop()?.toLowerCase();
  const byExtension: Record<string, string> = {
    jpg: 'image/jpeg',
    jpeg: 'image/jpeg',
    png: 'image/png',
    gif: 'image/gif',
    webp: 'image/webp',
  };
  return byExtension[extension || ''] || 'image/png';
};

const toRequestPart = (part: ThreadUserMessagePart): AgentRequestPart | null => {
  if (part.type === 'text' && part.text) {
    return { type: 'text', text: part.text };
  }
  if (part.type === 'file' && part.data && part.mimeType) {
    return {
      type: 'file',
      data: part.data,
      mediaType: part.mimeType,
    };
  }
  if (part.type === 'image' && part.image) {
    return {
      type: 'file',
      data: part.image,
      mediaType: imageMediaType(part.image),
    };
  }
  return null;
};

/**
 * Send only the new user turn. Historical text, artifacts and terminal
 * resources are server-owned and are reattached by conversation id.
 */
export function currentUserRequest(
  repository: ExportedMessageRepository | null | undefined,
): CurrentUserRequest | null {
  const entry = [...(repository?.messages || [])]
    .reverse()
    .find((item) => item.message.role === 'user');
  if (!entry || entry.message.role !== 'user') {
    return null;
  }
  const parts = [
    ...entry.message.content,
    ...entry.message.attachments.flatMap((attachment) => attachment.content),
  ].flatMap((part) => {
    const converted = toRequestPart(part);
    return converted ? [converted] : [];
  });
  if (!parts.length) {
    return null;
  }
  return {
    messages: [{
      id: entry.message.id,
      role: 'user',
      content: parts,
    }],
    historyParentId: entry.parentId,
  };
}
