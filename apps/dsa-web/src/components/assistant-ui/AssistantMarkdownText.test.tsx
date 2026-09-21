import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AssistantMarkdown, AssistantMarkdownText } from './AssistantMarkdownText';

describe('AssistantMarkdown evidence citations', () => {
  it('reveals live assistant text progressively before settling on the full sentence', async () => {
    const text = '我会先核对证券身份，再继续读取最新行情。';

    render(<AssistantMarkdown text={text} animate />);

    expect(screen.queryByText(text)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(text)).toBeInTheDocument());
  });

  it('does not replay a completed sentence when a live projection is replaced', async () => {
    const firstText = '我会先核对证券身份，再继续读取最新行情。';
    const replacementText = '我会先核对证券身份，然后汇总最新行情。';
    const view = render(<AssistantMarkdown text={firstText} animate />);

    await waitFor(() => expect(screen.getByText(firstText)).toBeInTheDocument());
    view.rerender(<AssistantMarkdown text={replacementText} animate />);

    await waitFor(() => expect(screen.getByText(replacementText)).toBeInTheDocument());
    expect(screen.queryByText(firstText)).not.toBeInTheDocument();
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

  it('keeps native answer parts readable when their trace arrives as metadata', () => {
    render(
      <AssistantMarkdownText
        type="text"
        status={{ type: 'complete' }}
        text="行情已核验【证据 ev_native】。"
        evidence={{
          evidence: [{ evidence_id: 'ev_native', action_id: 'native-action' }],
          tool_results: [{
            action_id: 'native-action',
            tool_name: 'read_realtime_quote',
            result_summary: '已返回最新行情。',
          }],
        }}
      />,
    );

    expect(screen.getByText('①')).toBeInTheDocument();
    expect(screen.queryByText(/ev_native/)).not.toBeInTheDocument();
  });

  it('does not expose an unresolved evidence ID in the chat projection', () => {
    render(
      <AssistantMarkdown
        text="部分结论暂时无法关联来源【证据 ev_missing】。"
        evidence={{ evidence: [] }}
      />,
    );

    expect(screen.queryByText(/ev_missing/)).not.toBeInTheDocument();
    expect(screen.getByText(/部分证据暂未关联/)).toBeInTheDocument();
  });
});
