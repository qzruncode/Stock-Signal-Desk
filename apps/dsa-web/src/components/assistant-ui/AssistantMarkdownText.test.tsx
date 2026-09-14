import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AssistantMarkdown } from './AssistantMarkdownText';

describe('AssistantMarkdown evidence citations', () => {
  it('reveals live assistant text progressively before settling on the full sentence', async () => {
    const text = '我会先核对证券身份，再继续读取最新行情。';

    render(<AssistantMarkdown text={text} animate />);

    expect(screen.queryByText(text)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(text)).toBeInTheDocument());
  });

  it('hides the raw ID and reveals the evidence summary on hover', async () => {
    render(
      <AssistantMarkdown
        text="指数已经回稳【ev_market】。"
        evidence={{
          evidence: [{ evidence_id: 'ev_market0123456789', action_id: 'market-1' }],
          tool_results: [{
            action_id: 'market-1',
            tool_name: 'read_market_indices',
            result_summary: '已返回主要指数行情。',
            source_labels: ['新浪财经'],
            result_items: [{
              title: '上证指数',
              attributes: [{ name: 'price', value: '3942.0879' }],
            }],
          }],
        }}
      />,
    );

    expect(screen.getByText('①')).toBeInTheDocument();
    expect(screen.getByText('①')).not.toHaveClass('rounded-full');
    expect(screen.getByLabelText('查看证据 ①')).toHaveClass('cursor-help');
    expect(screen.queryByText(/ev_market/)).not.toBeInTheDocument();

    fireEvent.pointerEnter(screen.getByLabelText('查看证据 ①'));
    await waitFor(() => {
      const tooltip = screen.getByRole('tooltip');
      expect(tooltip).toHaveTextContent('参考内容 · ①');
      expect(tooltip).toHaveTextContent('已返回主要指数行情。');
      expect(tooltip).toHaveTextContent('新浪财经');
      expect(tooltip).toHaveTextContent('最新价：3942.0879');

      const metadata = tooltip.querySelector('[data-evidence-metadata]');
      expect(metadata).not.toBeNull();
      expect(metadata).toHaveTextContent('工具：read_market_indices');
      expect(metadata).toHaveClass('text-[10px]', 'text-muted-foreground');
    });
  });
});
