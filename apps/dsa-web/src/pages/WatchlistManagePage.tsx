import React, { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { InlineAlert } from '../components/common';
import AtrScreenerDialog from '../components/AtrScreenerDialog';
import WatchlistManageDrawer from '../components/watchlist/WatchlistManageDrawer';
import GroupTabs from '../components/watchlist/GroupTabs';
import StockSearchBar from '../components/watchlist/StockSearchBar';
import StockCardGrid from '../components/watchlist/StockCardGrid';
import { useWatchlistManage, DEFAULT_GROUP_ID } from '../hooks/useWatchlistManage';

const WatchlistManagePage: React.FC = () => {
  const navigate = useNavigate();
  const {
    watchlist,
    isLoading,
    error,
    successMsg,
    groups,
    activeGroupId,
    isAdding,
    isBatchAdding,
    isBatchRemoving,
    removingCodes,
    newGroupName,
    batchInput,
    selectedCodes,
    renameValue,
    suggestInputRef,
    suggestContainerRef,
    suggestLoading,
    suggestions,
    suggestOpen,
    handleInputChange,
    activeGroup,
    displayStocks,
    watchlistCodes,
    allGroupCodes,
    setError,
    setActiveGroupId,
    setNewGroupName,
    setBatchInput,
    setRenameValue,
    setSelectedCodes,
    holdMessage,
    handleAddStock,
    handleRemoveFromGroup,
    handleCreateGroup,
    handleQuickAddGroup,
    handleDeleteGroup,
    handleRenameGroup,
    handleBatchAdd,
    handleBatchRemove,
    toggleSelect,
    handleOpenDrawer,
  } = useWatchlistManage();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [screenerOpen, setScreenerOpen] = useState(false);

  useEffect(() => {
    document.title = '自选分组管理 - Stock-Signal-Desk';
  }, []);

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-2rem)] w-full max-w-[960px] flex-col gap-3 overflow-hidden px-3 py-4 sm:px-5">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-4">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 transition hover:border-cyan-300 hover:text-cyan-700"
          aria-label="返回首页"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div className="flex-1">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Portfolio</p>
          <h1 className="text-2xl font-semibold text-slate-950">自选分组管理</h1>
          <p className="mt-1 text-sm text-slate-500">管理股票分组，用于跑批分析时选择目标股票集合</p>
        </div>
      </div>

      {/* Alerts */}
      <div className="shrink-0 space-y-2">
        {error ? (
          <InlineAlert variant="danger" title="操作失败" message={error} className="rounded-xl px-3 py-2 text-xs shadow-none" />
        ) : null}
        {successMsg ? (
          <InlineAlert variant="success" title="操作成功" message={successMsg} className="rounded-xl px-3 py-2 text-xs shadow-none" />
        ) : null}
      </div>

      {/* Group tabs */}
      <GroupTabs
        groups={groups}
        activeGroupId={activeGroupId}
        watchlist={watchlist}
        onChangeGroup={(id) => { setActiveGroupId(id); setSelectedCodes(new Set()); }}
        onQuickAddGroup={handleQuickAddGroup}
        onOpenScreener={() => setScreenerOpen(true)}
      />

      {/* ATR Screener Dialog */}
      <AtrScreenerDialog
        onError={(msg) => setError(msg)}
        onSuccess={holdMessage}
        onGroupSelect={setActiveGroupId}
        isOpen={screenerOpen}
        onClose={() => setScreenerOpen(false)}
      />

      {/* Search + add bar */}
      <StockSearchBar
        suggestInputRef={suggestInputRef}
        suggestContainerRef={suggestContainerRef}
        suggestLoading={suggestLoading}
        suggestions={suggestions}
        suggestOpen={suggestOpen}
        handleInputChange={handleInputChange}
        isAdding={isAdding}
        handleAddStock={handleAddStock}
        watchlistCodes={watchlistCodes}
        allGroupCodes={allGroupCodes}
        activeGroupId={activeGroupId}
        onOpenDrawer={() => { setDrawerOpen(true); handleOpenDrawer(); }}
      />

      {/* Stock card grid */}
      <StockCardGrid
        stocks={displayStocks}
        activeGroupName={activeGroup?.name ?? null}
        isDefaultGroup={activeGroupId === DEFAULT_GROUP_ID}
        removingCodes={removingCodes}
        onRemove={handleRemoveFromGroup}
      />

      <WatchlistManageDrawer
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        defaultGroupId={DEFAULT_GROUP_ID}
        defaultGroupName="我的自选股"
        activeGroupId={activeGroupId}
        activeGroup={activeGroup}
        renameValue={renameValue}
        onRenameValueChange={setRenameValue}
        onRenameGroup={handleRenameGroup}
        onDeleteGroup={handleDeleteGroup}
        newGroupName={newGroupName}
        onNewGroupNameChange={setNewGroupName}
        onCreateGroup={handleCreateGroup}
        batchInput={batchInput}
        onBatchInputChange={setBatchInput}
        onBatchAdd={handleBatchAdd}
        isBatchAdding={isBatchAdding}
        displayStocks={displayStocks}
        selectedCodes={selectedCodes}
        onToggleSelect={toggleSelect}
        onBatchRemove={handleBatchRemove}
        isBatchRemoving={isBatchRemoving}
      />
    </div>
  );
};

export default WatchlistManagePage;