import { describe, expect, it } from 'vitest';
import {
  assistantAnswerTextFromContent,
  assistantPostToolBodyText,
} from './assistantAnswer';

const progress = (text: string, roundId: string) => ({
  type: 'text',
  text,
  providerMetadata: { dsa: { displayKind: 'progress', roundId } },
});

const answer = (text: string, roundId: string) => ({
  type: 'text',
  text,
  providerMetadata: { dsa: { displayKind: 'answer', roundId } },
});

describe('assistant answer presentation boundary', () => {
  it('uses the published answer instead of duplicating a pre-publication draft', () => {
    const content = [
      progress('先说明计划。', '1'),
      { type: 'tool-call' },
      progress('完整的分析正文。', '2'),
      progress('证据校验摘要。', '3'),
      answer('证据校验摘要。\n\n[存在证据缺口]', '4'),
    ];

    expect(assistantPostToolBodyText(content)).toBe('完整的分析正文。');
    expect(assistantAnswerTextFromContent(content)).toBe('证据校验摘要。\n\n[存在证据缺口]');
  });

  it('falls back to the first post-tool body when no answer boundary exists', () => {
    const content = [
      { type: 'tool-call' },
      progress('完整的分析正文。', '1'),
      progress('后续执行提示。', '2'),
    ];

    expect(assistantAnswerTextFromContent(content)).toBe('完整的分析正文。');
  });

  it('does not duplicate a published answer already represented by the body', () => {
    const content = [
      { type: 'tool-call' },
      progress('最终答案', '1'),
      answer('最终答案', '1'),
    ];

    expect(assistantAnswerTextFromContent(content)).toBe('最终答案');
  });

  it('keeps only the answer after the latest server publication boundary', () => {
    const content = [
      { type: 'data', name: 'agent-answer-boundary' },
      { type: 'text', text: '第一次完整回答' },
      { type: 'data', name: 'agent-answer-boundary' },
      { type: 'text', text: '第二次正式回答' },
    ];

    expect(assistantAnswerTextFromContent(content)).toBe('第二次正式回答');
  });
});
