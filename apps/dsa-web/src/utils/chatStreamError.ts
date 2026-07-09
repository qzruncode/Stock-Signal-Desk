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
