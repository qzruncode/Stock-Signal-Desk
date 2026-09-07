import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { researchApi } from '../api/research';
import { toApiErrorMessage } from '../api/error';
import { ResearchAlerts } from '../components/research/ResearchAlerts';
import { AssistantMarkdown } from '../components/assistant-ui/AssistantMarkdownText';

export default function ResearchPage() {
  useEffect(() => {
    document.title = '研究档案 - Stock Assistant';
  }, []);
  const [symbol, setSymbol] = useState('');
  const [filter, setFilter] = useState('');
  const client = useQueryClient();
  const notes = useQuery({
    queryKey: ['research', filter],
    queryFn: ({ signal }) => researchApi.notes(filter, signal),
  });
  const refresh = useMutation({
    mutationFn: researchApi.refresh,
    onSuccess: () => client.invalidateQueries({ queryKey: ['research'] }),
  });
  return (
    <div className="mx-auto max-w-6xl space-y-6 py-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">研究档案</h1>
        <p className="text-sm text-muted-foreground">
          保留每次判断、当时证据与后续表现。同一股票按时间排列，可对照结论变化。
        </p>
      </header>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setFilter(symbol.trim());
        }}
      >
        <input
          aria-label="股票代码"
          value={symbol}
          onChange={(event) => setSymbol(event.target.value)}
          placeholder="股票代码，留空查看全部"
          className="rounded border border-border bg-background px-3 py-2"
        />
        <button className="rounded border border-border px-3 py-2">筛选</button>
        <button
          type="button"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
          className="rounded border border-border px-3 py-2"
        >
          {refresh.isPending ? '更新中…' : '更新复盘'}
        </button>
      </form>
      {(notes.error || refresh.error) && (
        <p role="alert">{toApiErrorMessage(notes.error || refresh.error, '研究数据加载失败')}</p>
      )}
      {notes.isPending && <p>加载研究记录…</p>}
      {notes.data?.length === 0 && (
        <p className="rounded border border-dashed border-border p-6 text-muted-foreground">
          还没有已通过核验的结构化研究结论。完成带有明确股票判断的分析后，将自动归档；不会从旧报告猜测结论。
        </p>
      )}
      <section aria-label="研究记录" className="grid gap-4">
        {notes.data?.map((note) => (
          <article key={note.id} className="space-y-3 rounded-xl border border-border p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-medium">
                {note.symbol} ·{' '}
                {{ buy: '考虑买入', not_buy: '暂不买入', watch: '持续观察', avoid: '回避' }[note.verdict] ||
                  note.verdict}
              </h2>
              <span className="text-xs text-muted-foreground">{note.as_of_at.replace('T', ' ')}</span>
            </div>
            {note.thesis.blocks?.map((block, index) => (
              <AssistantMarkdown key={index} text={block.content} />
            ))}
            <div className="flex flex-wrap gap-3 text-sm">
              {note.outcomes.map((outcome) => (
                <span key={outcome.horizon_trading_days}>
                  {outcome.horizon_trading_days} 个交易日：
                  {outcome.status === 'completed' && outcome.return_pct != null
                    ? `${outcome.return_pct.toFixed(2)}%`
                    : '待积累行情'}
                </span>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              基准：{note.baseline_trade_date || '缺少行情'} / {note.baseline_price ?? '—'}
              。采用记录中的日线基准；新研究只取结论日期之前的有效日线。参考收益含基准与结论时刻之间的价格变化，不代表成交收益或策略回测。
            </p>
            <div className="flex flex-wrap gap-3 text-xs">
              <Link to={`/runs?runId=${encodeURIComponent(note.run_id)}`} className="text-primary underline">
                查看原始运行
              </Link>
              {[...new Set(note.evidence.items?.flatMap((item) => item.source_refs || []) || [])]
                .filter((url) => /^https?:\/\//i.test(url))
                .map((url, index) => (
                  <a href={url} key={url} target="_blank" rel="noopener noreferrer" className="text-primary underline">
                    来源 {index + 1}
                  </a>
                ))}
            </div>
          </article>
        ))}
      </section>
      <ResearchAlerts />
    </div>
  );
}
