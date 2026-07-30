import { extractErrorPayloadText } from '../api/error';

/**
 * 从流式响应的 Response 中读取错误文案:优先解析后端结构化错误 payload,
 * 回退到原始文本。供 ChatHomePage 的 onResponse 使用。
 */
export async function readStreamErrorMessage(response: Response): Promise<string> {
  const rawText = await response.clone().text().catch(() => '');
  if (!rawText.trim()) {
    return `请求失败：HTTP ${response.status}`;
  }

  try {
    const payload = JSON.parse(rawText) as unknown;
    return extractErrorPayloadText(payload) || rawText;
  } catch {
    return rawText;
  }
}

/** Extract the backend's structured message from data-stream adapter errors. */
export function readThrownStreamErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error || '');
  const jsonStart = raw.indexOf('{');
  if (jsonStart >= 0) {
    try {
      const payload = JSON.parse(raw.slice(jsonStart)) as unknown;
      const message = extractErrorPayloadText(payload);
      if (message) return message;
    } catch {
      // Fall through to the adapter's original message.
    }
  }
  return raw || '对话请求失败，请稍后重试';
}

/** Only explicit aborts mean that the local stream was intentionally cancelled. */
export function isStreamAbortError(error: unknown): boolean {
  return typeof error === 'object'
    && error !== null
    && 'name' in error
    && error.name === 'AbortError';
}
