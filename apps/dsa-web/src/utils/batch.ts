import type { BatchResultItem } from '../api/batch';

export function summarizeResult(text: string): string {
  const compact = text
    .split('\n')
    .map((line) => line.replace(/^#+\s*/, '').trim())
    .find((line) => line.length > 0) || '无摘要';
  return compact.length > 88 ? `${compact.slice(0, 87)}...` : compact;
}

export function parseBatchResults(raw: string | null | undefined): BatchResultItem[] {
  if (!raw || raw === '[]' || raw === '{}') return [];
  try {
    const parsed = JSON.parse(raw) as Record<string, { success?: boolean; model?: string; text?: string; decision?: string }>;
    return Object.entries(parsed)
      .filter(([code, result]) => code !== '__all__' && result && typeof result === 'object')
      .map(([code, result]) => {
        const text = result.text || '';
        return {
          code,
          success: Boolean(result.success),
          model: result.model || '-',
          text,
          summary: summarizeResult(text),
          decision: result.decision,
        };
      });
  } catch {
    return [];
  }
}

export function extractPassedCodesFromSummary(summaryMd: string): string[] {
  const lines = summaryMd.split('\n');
  const start = lines.findIndex((line) => /^##+\s+筛选通过股票/.test(line.trim()));
  if (start < 0) return [];
  const codes: string[] = [];
  for (const line of lines.slice(start + 1)) {
    const trimmed = line.trim();
    if (/^##+\s+/.test(trimmed)) break;
    if (!trimmed.startsWith('|') || trimmed.includes('---')) continue;
    const cells = trimmed.split('|').map((cell) => cell.trim()).filter(Boolean);
    const code = cells[0];
    if (/^[A-Za-z0-9.]+$/.test(code) && code !== '股票') {
      codes.push(code);
    }
  }
  return Array.from(new Set(codes));
}