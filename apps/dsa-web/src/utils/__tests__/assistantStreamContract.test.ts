import { describe, expect, it } from 'vitest';
import { createAssistantStreamController } from 'assistant-stream';

describe('assistant-stream close contract', () => {
  it('ignores late text after close while preserving completed stream content', async () => {
    const [stream, assistant] = createAssistantStreamController();
    const controller = assistant.addTextPart();
    controller.append('已完成');
    controller.close();

    expect(() => controller.append('迟到内容')).not.toThrow();
    assistant.close();

    const reader = stream.getReader();
    const chunks = [];
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      chunks.push(chunk.value);
    }
    expect(chunks.filter((chunk) => chunk.type === 'text-delta')).toEqual([
      { type: 'text-delta', path: [0], textDelta: '已完成' },
    ]);
    expect(chunks.some((chunk) => chunk.type === 'part-finish')).toBe(true);
  });
});
