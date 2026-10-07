import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { Check, X } from 'lucide-react';
import { StockListPanels } from './StockListPanels';
import { StockListGroupDialogs } from './StockListGroupDialogs';
import { stocksApi, type StockMetaItem, type StocksListResponse } from '../../api/stocks';
import { watchlistApi, type WatchlistGroup, type WatchlistResponse } from '../../api/watchlist';
import { useStockIndex } from '../../hooks/useStockIndex';
import { cn } from '../../utils/cn';

type ViewMode = 'market' | 'watchlist';

interface MarketFormValues {
  search: string;
  market: string;
}

interface WatchlistFormValues {
  activeGroupId: string;
  newGroupName: string;
  renameValue: string;
  addQuery: string;
  batchInput: string;
}

const DEFAULT_GROUP_ID = 'default';
const DEFAULT_GROUP_NAME = '我的自选股';
const MARKET_OPTIONS = [
  { value: '', label: '全部市场' },
  { value: 'sh', label: '沪市主板' },
  { value: 'sz', label: '深市主板' },
  { value: 'cyb', label: '创业板' },
  { value: 'kcb', label: '科创板' },
  { value: 'bj', label: '北交所' },
];

function marketLabel(market: string): string {
  return MARKET_OPTIONS.find((item) => item.value === market)?.label || market || '其他';
}

function readableError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function uniqueValues(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function groupName(group: WatchlistGroup | null): string {
  const name = group?.name?.trim();
  return !name || name.toLowerCase() === 'null' || name.toLowerCase() === 'undefined'
    ? '未命名分组'
    : name;
}

function exportFileName(value: string): string {
  return value.replace(/[\\/:*?"<>|]/g, '-').trim() || '股票分组';
}

function downloadMarkdown(markdown: string, fileName: string): void {
  const blobUrl = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(blobUrl);
}

function markdownCell(value: unknown): string {
  return String(value ?? '-').replace(/[|\r\n]/g, (character) => (character === '|' ? '\\|' : ' '));
}

function StockListSettingsView() {
  const [view, setView] = useState<ViewMode>('market');
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const [marketSearch, setMarketSearch] = useState('');
  const [marketFilter, setMarketFilter] = useState('');
  const [marketPage, setMarketPage] = useState(1);
  const [marketData, setMarketData] = useState<StocksListResponse | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);

  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [watchlistLoading, setWatchlistLoading] = useState(true);
  const [candidateItems, setCandidateItems] = useState<StockMetaItem[]>([]);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const candidateRequestRef = useRef(0);
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WatchlistGroup | null>(null);
  const [pendingActiveGroupId, setPendingActiveGroupId] = useState<string | null>(null);
  const [exportConfirming, setExportConfirming] = useState(false);
  const [marketExportConfirming, setMarketExportConfirming] = useState(false);
  const { index: stockIndex } = useStockIndex();
  const stockNameByCode = useMemo(
    () => new Map(stockIndex.map((item) => [item.displayCode, item.nameZh])),
    [stockIndex],
  );
  const marketForm = useForm<MarketFormValues>({
    defaultValues: { search: '', market: '' },
  });
  const marketValues = useWatch({ control: marketForm.control });
  const watchlistForm = useForm<WatchlistFormValues>({
    defaultValues: {
      activeGroupId: DEFAULT_GROUP_ID,
      newGroupName: '',
      renameValue: '',
      addQuery: '',
      batchInput: '',
    },
  });
  const watchlistValues = useWatch({ control: watchlistForm.control });
  const activeGroupId = watchlistValues.activeGroupId || DEFAULT_GROUP_ID;

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), 2800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const loadMarket = useCallback(async () => {
    setMarketLoading(true);
    try {
      const result = await stocksApi.list({
        page: marketPage,
        page_size: 50,
        search: marketSearch || undefined,
        market: marketFilter || undefined,
      });
      setMarketData(result);
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '股票列表加载失败') });
    } finally {
      setMarketLoading(false);
    }
  }, [marketFilter, marketPage, marketSearch]);

  const loadWatchlist = useCallback(async () => {
    setWatchlistLoading(true);
    try {
      const [watchlistResult, groupResult] = await Promise.all([
        watchlistApi.get(),
        watchlistApi.listGroups(),
      ]);
      setWatchlist(watchlistResult);
      setGroups(groupResult);
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '自选股加载失败') });
    } finally {
      setWatchlistLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMarket();
  }, [loadMarket]);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  useEffect(() => {
    if (activeGroupId !== DEFAULT_GROUP_ID && !groups.some((group) => group.id === activeGroupId)) {
      watchlistForm.setValue('activeGroupId', DEFAULT_GROUP_ID);
    }
  }, [activeGroupId, groups, watchlistForm]);

  useEffect(() => {
    if (!pendingActiveGroupId || !groups.some((group) => group.id === pendingActiveGroupId)) return;
    watchlistForm.setValue('activeGroupId', pendingActiveGroupId);
    setPendingActiveGroupId(null);
  }, [groups, pendingActiveGroupId, watchlistForm]);

  const watchlistCodes = useMemo(() => new Set(watchlist?.codes || []), [watchlist]);
  const activeGroup = useMemo<WatchlistGroup | null>(() => {
    if (activeGroupId === DEFAULT_GROUP_ID) {
      return {
        id: DEFAULT_GROUP_ID,
        name: DEFAULT_GROUP_NAME,
        codes: watchlist?.codes || [],
      };
    }
    return groups.find((group) => group.id === activeGroupId) || null;
  }, [activeGroupId, groups, watchlist]);
  const activeCodes = useMemo(() => activeGroup?.codes || [], [activeGroup]);
  const activeCodeCount = uniqueValues(activeCodes).length;

  const exportActiveGroupMarkdown = useCallback(() => {
    setExportConfirming(false);
    if (!activeGroup) return;
    const codes = uniqueValues(activeCodes);
    if (!codes.length) {
      setNotice({ type: 'error', message: '当前分组没有可导出的股票代码。' });
      return;
    }
    const markdown = [
      `# ${groupName(activeGroup)}`,
      '',
      `股票数量：${codes.length}`,
      '',
      '## 股票代码',
      '',
      ...codes.map((code) => `- ${code}`),
      '',
    ].join('\n');
    downloadMarkdown(markdown, `${exportFileName(groupName(activeGroup))}-股票代码.md`);
    setNotice({ type: 'success', message: `已导出「${groupName(activeGroup)}」的 ${codes.length} 个股票代码。` });
  }, [activeCodes, activeGroup]);

  const exportAllMarketMarkdown = useCallback(async () => {
    setMarketExportConfirming(false);
    setBusyAction('export-market');
    setNotice(null);
    try {
      const items = await stocksApi.listAll({
        search: marketSearch || undefined,
        market: marketFilter || undefined,
      });
      if (!items.length) {
        setNotice({ type: 'error', message: '当前范围没有可导出的股票。' });
        return;
      }

      const hasFilter = Boolean(marketSearch || marketFilter);
      const title = hasFilter ? '全市场股票（当前筛选）' : '全市场股票';
      const filterLines = [
        `市场范围：${marketFilter ? marketLabel(marketFilter) : '全部市场'}`,
        marketSearch ? `搜索条件：${marketSearch}` : null,
      ].filter(Boolean);
      const markdown = [
        `# ${title}`,
        '',
        `股票数量：${items.length}`,
        ...filterLines,
        '',
        '| 股票代码 | 股票名称 | 市场 | 行业 | 状态 |',
        '| --- | --- | --- | --- | --- |',
        ...items.map((stock) => `| ${markdownCell(stock.code)} | ${markdownCell(stock.name)} | ${markdownCell(marketLabel(stock.market))} | ${markdownCell(stock.sector || '行业未标注')} | ${markdownCell(stock.status === 'active' ? '正常' : stock.status || '未知')} |`),
        '',
      ].join('\n');
      downloadMarkdown(markdown, `${exportFileName(title)}-股票列表.md`);
      setNotice({ type: 'success', message: `已导出「${title}」的 ${items.length} 只股票。` });
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '全市场股票导出失败') });
    } finally {
      setBusyAction(null);
    }
  }, [marketFilter, marketSearch]);

  const submitMarketSearch = useCallback((values: MarketFormValues) => {
    setMarketExportConfirming(false);
    setMarketPage(1);
    setMarketSearch(values.search.trim());
  }, []);

  const addToDefaultWatchlist = useCallback(async (code: string) => {
    setBusyAction(`add:${code}`);
    setNotice(null);
    try {
      const result = await watchlistApi.add([code]);
      setWatchlist({
        codes: result.codes,
        count: result.count,
        configVersion: result.configVersion,
      });
      setNotice({ type: 'success', message: result.added.length ? `已加入自选股：${result.added.join('、')}` : `${code} 已在自选股中。` });
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '添加自选股失败') });
    } finally {
      setBusyAction(null);
    }
  }, []);

  const addToActiveGroup = useCallback(async (rawCodes: string[]) => {
    const codes = uniqueValues(rawCodes);
    if (!codes.length || !activeGroup) return;
    setBusyAction('add-group');
    setNotice(null);
    try {
      if (activeGroup.id === DEFAULT_GROUP_ID) {
        const result = await watchlistApi.add(codes);
        setWatchlist({
          codes: result.codes,
          count: result.count,
          configVersion: result.configVersion,
        });
        setNotice({ type: 'success', message: result.added.length ? `已添加 ${result.added.length} 只股票。` : '股票已经在默认自选股中。' });
      } else {
        const groupCodes = uniqueValues([...activeGroup.codes, ...codes]);
        await watchlistApi.updateGroup(activeGroup.id, { codes: groupCodes });
        await loadWatchlist();
        setNotice({ type: 'success', message: `已添加到「${groupName(activeGroup)}」。` });
      }
      watchlistForm.setValue('addQuery', '');
      setCandidateItems([]);
      watchlistForm.setValue('batchInput', '');
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '添加股票失败') });
    } finally {
      setBusyAction(null);
    }
  }, [activeGroup, loadWatchlist, watchlistForm]);

  const findCandidates = useCallback(async (rawQuery: string) => {
    const query = rawQuery.trim();
    const requestId = candidateRequestRef.current + 1;
    candidateRequestRef.current = requestId;
    if (!query) {
      setCandidateItems([]);
      setCandidateLoading(false);
      return;
    }
    setCandidateLoading(true);
    setNotice(null);
    try {
      const result = await stocksApi.list({ page: 1, page_size: 10, search: query });
      if (requestId !== candidateRequestRef.current) return;
      setCandidateItems(result.items);
    } catch (error) {
      if (requestId !== candidateRequestRef.current) return;
      setNotice({ type: 'error', message: readableError(error, '股票搜索失败') });
    } finally {
      if (requestId === candidateRequestRef.current) setCandidateLoading(false);
    }
  }, []);

  useEffect(() => {
    const query = watchlistValues.addQuery || '';
    if (!query.trim()) {
      candidateRequestRef.current += 1;
      setCandidateItems([]);
      setCandidateLoading(false);
      return undefined;
    }
    const timer = window.setTimeout(() => {
      void findCandidates(query);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [findCandidates, watchlistValues.addQuery]);

  const removeFromActiveGroup = useCallback(async (code: string) => {
    if (!activeGroup) return;
    setBusyAction(`remove:${code}`);
    setNotice(null);
    try {
      if (activeGroup.id === DEFAULT_GROUP_ID) {
        const result = await watchlistApi.remove([code]);
        setWatchlist({
          codes: result.codes,
          count: result.count,
          configVersion: result.configVersion,
        });
        await Promise.all(
          groups
            .filter((group) => group.codes.includes(code))
            .map((group) => watchlistApi.updateGroup(group.id, {
              codes: group.codes.filter((item) => item !== code),
            })),
        );
        if (groups.some((group) => group.codes.includes(code))) await loadWatchlist();
      } else {
        await watchlistApi.updateGroup(activeGroup.id, {
          codes: activeGroup.codes.filter((item) => item !== code),
        });
        await loadWatchlist();
      }
      setNotice({ type: 'success', message: `已从「${groupName(activeGroup)}」移除 ${code}。` });
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '移除股票失败') });
    } finally {
      setBusyAction(null);
    }
  }, [activeGroup, groups, loadWatchlist]);

  const createGroup = useCallback(async (values: WatchlistFormValues) => {
    const name = values.newGroupName.trim();
    if (!name) {
      setNotice({ type: 'error', message: '请输入分组名称。' });
      return;
    }
    if (groups.some((group) => group.name === name)) {
      setNotice({ type: 'error', message: `分组「${name}」已经存在。` });
      return;
    }
    setBusyAction('create-group');
    try {
      const created = await watchlistApi.createGroup(name);
      setPendingActiveGroupId(created.id);
      await loadWatchlist();
      watchlistForm.setValue('newGroupName', '');
      setCreateGroupOpen(false);
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '创建分组失败') });
    } finally {
      setBusyAction(null);
    }
  }, [groups, loadWatchlist, watchlistForm]);

  const renameGroup = useCallback(async (values: WatchlistFormValues) => {
    if (!editingGroupId) return;
    const group = groups.find((item) => item.id === editingGroupId);
    const name = values.renameValue.trim();
    if (!group || !name) return;
    if (groups.some((item) => item.id !== editingGroupId && groupName(item) === name)) {
      setNotice({ type: 'error', message: `分组「${name}」已经存在。` });
      return;
    }
    setBusyAction('rename-group');
    try {
      await watchlistApi.updateGroup(editingGroupId, { name });
      await loadWatchlist();
      setEditingGroupId(null);
      watchlistForm.setValue('renameValue', '');
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '分组重命名失败') });
    } finally {
      setBusyAction(null);
    }
  }, [editingGroupId, groups, loadWatchlist, watchlistForm]);

  const deleteGroup = useCallback(async (group: WatchlistGroup) => {
    if (group.id === DEFAULT_GROUP_ID) return;
    setBusyAction(`delete-group:${group.id}`);
    setNotice(null);
    try {
      await watchlistApi.deleteGroup(group.id);
      if (activeGroupId === group.id) watchlistForm.setValue('activeGroupId', DEFAULT_GROUP_ID);
      await loadWatchlist();
    } catch (error) {
      setNotice({ type: 'error', message: readableError(error, '删除分组失败') });
    } finally {
      setBusyAction(null);
    }
  }, [activeGroupId, loadWatchlist, watchlistForm]);

  const closeEditGroup = useCallback(() => {
    if (busyAction === 'rename-group') return;
    setEditingGroupId(null);
    watchlistForm.setValue('renameValue', '');
    watchlistForm.clearErrors('renameValue');
  }, [busyAction, watchlistForm]);

  const submitBatchAdd = useCallback(async (values: WatchlistFormValues) => {
    await addToActiveGroup(values.batchInput.split(/[\n,，\s]+/));
  }, [addToActiveGroup]);

  return (
    <section className="flex h-full min-h-0 flex-col space-y-3 text-xs">
      {notice ? (
        <div className="pointer-events-none fixed inset-x-0 top-2 z-[70] flex justify-center px-3">
          <div
            role={notice.type === 'success' ? 'status' : 'alert'}
            aria-live="polite"
            className={cn(
              'flex max-w-[calc(100vw-1.5rem)] items-center gap-2 rounded-full border px-3 py-2 text-[11px] font-medium shadow-lg backdrop-blur-md',
              notice.type === 'success'
                ? 'border-success/25 bg-emerald-50/95 text-success'
                : 'border-danger/25 bg-red-50/95 text-danger',
            )}
          >
            {notice.type === 'success' ? <Check className="size-3.5 shrink-0" /> : <X className="size-3.5 shrink-0" />}
            <span className="truncate">{notice.message}</span>
          </div>
        </div>
      ) : null}

      <StockListPanels
        view={view}
        setView={setView}
        setMarketSearch={setMarketSearch}
        setMarketFilter={setMarketFilter}
        setMarketPage={setMarketPage}
        setMarketExportConfirming={setMarketExportConfirming}
        marketSearch={marketSearch}
        marketFilter={marketFilter}
        marketPage={marketPage}
        marketForm={marketForm}
        marketValues={marketValues}
        submitMarketSearch={submitMarketSearch}
        marketData={marketData}
        marketLoading={marketLoading}
        busyAction={busyAction}
        marketExportConfirming={marketExportConfirming}
        exportAllMarketMarkdown={exportAllMarketMarkdown}
        addToDefaultWatchlist={addToDefaultWatchlist}
        watchlistCodes={watchlistCodes}
        watchlistForm={watchlistForm}
        watchlistValues={watchlistValues}
        watchlist={watchlist}
        groups={groups}
        setNotice={setNotice}
        setEditingGroupId={setEditingGroupId}
        setDeleteTarget={setDeleteTarget}
        setCreateGroupOpen={setCreateGroupOpen}
        candidateLoading={candidateLoading}
        candidateItems={candidateItems}
        watchlistLoading={watchlistLoading}
        activeCodes={activeCodes}
        activeGroup={activeGroup}
        addToActiveGroup={addToActiveGroup}
        submitBatchAdd={submitBatchAdd}
        stockNameByCode={stockNameByCode}
        removeFromActiveGroup={removeFromActiveGroup}
        exportConfirming={exportConfirming}
        setExportConfirming={setExportConfirming}
        activeCodeCount={activeCodeCount}
        exportActiveGroupMarkdown={exportActiveGroupMarkdown}
      />

      <StockListGroupDialogs
        deleteTarget={deleteTarget}
        setDeleteTarget={setDeleteTarget}
        deleteGroup={deleteGroup}
        createGroupOpen={createGroupOpen}
        setCreateGroupOpen={setCreateGroupOpen}
        busyAction={busyAction}
        watchlistForm={watchlistForm}
        createGroup={createGroup}
        editingGroupId={editingGroupId}
        closeEditGroup={closeEditGroup}
        renameGroup={renameGroup}
      />
    </section>
  );
}

export default StockListSettingsView;
