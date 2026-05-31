import { Plus } from 'lucide-react';
import { Button, Drawer } from '../common';
import { cn } from '../../utils/cn';
import type { WatchlistGroup } from '../../utils/watchlistGroups';

interface DisplayStock {
  code: string;
  marketLabel: string;
}

interface WatchlistManageDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  defaultGroupId: string;
  defaultGroupName: string;
  activeGroupId: string;
  activeGroup: WatchlistGroup | null;
  renameValue: string;
  onRenameValueChange: (value: string) => void;
  onRenameGroup: (name: string) => void;
  onDeleteGroup: () => void;
  newGroupName: string;
  onNewGroupNameChange: (value: string) => void;
  onCreateGroup: () => void;
  batchInput: string;
  onBatchInputChange: (value: string) => void;
  onBatchAdd: () => void;
  isBatchAdding: boolean;
  displayStocks: DisplayStock[];
  selectedCodes: Set<string>;
  onToggleSelect: (code: string) => void;
  onBatchRemove: () => void;
  isBatchRemoving: boolean;
}

export default function WatchlistManageDrawer({
  isOpen,
  onClose,
  defaultGroupId,
  defaultGroupName,
  activeGroupId,
  activeGroup,
  renameValue,
  onRenameValueChange,
  onRenameGroup,
  onDeleteGroup,
  newGroupName,
  onNewGroupNameChange,
  onCreateGroup,
  batchInput,
  onBatchInputChange,
  onBatchAdd,
  isBatchAdding,
  displayStocks,
  selectedCodes,
  onToggleSelect,
  onBatchRemove,
  isBatchRemoving,
}: WatchlistManageDrawerProps) {
  const isDefaultGroup = activeGroupId === defaultGroupId;

  return (
    <Drawer isOpen={isOpen} onClose={onClose} title="管理分组" width="max-w-lg">
      <div className="space-y-6">
        {isDefaultGroup ? (
          <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="text-sm font-semibold text-slate-800">{defaultGroupName}</h3>
            <p className="text-xs text-slate-500">
              这是默认分组，与全市场股票页的"已添加"状态同步。在此分组中增删股票，会实时同步到 stocks 页面。不可改名或删除。
            </p>
          </div>
        ) : null}

        {activeGroup && !isDefaultGroup ? (
          <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="text-sm font-semibold text-slate-800">当前分组：{activeGroup.name}</h3>
            <div className="flex gap-2">
              <input
                value={renameValue}
                onChange={(event) => onRenameValueChange(event.target.value)}
                placeholder="新名称"
                className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
              />
              <Button
                variant="secondary"
                size="sm"
                disabled={!renameValue.trim() || renameValue === activeGroup.name}
                onClick={() => onRenameGroup(renameValue)}
              >
                重命名
              </Button>
            </div>
            <Button variant="danger-subtle" size="sm" onClick={onDeleteGroup}>
              删除此分组
            </Button>
          </div>
        ) : null}

        <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
          <h3 className="text-sm font-semibold text-slate-800">新建分组</h3>
          <div className="flex gap-2">
            <input
              value={newGroupName}
              onChange={(event) => onNewGroupNameChange(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter') onCreateGroup(); }}
              placeholder="分组名称"
              className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
            />
            <Button variant="home-action-ai" size="sm" disabled={!newGroupName.trim()} onClick={onCreateGroup}>
              创建
            </Button>
          </div>
        </div>

        <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
          <h3 className="text-sm font-semibold text-slate-800">批量添加股票</h3>
          <textarea
            value={batchInput}
            onChange={(event) => onBatchInputChange(event.target.value)}
            placeholder="粘贴股票代码，换行/逗号/空格分隔&#10;例如：&#10;600519&#10;300750&#10;000858"
            rows={4}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
          />
          <Button
            variant="home-action-ai"
            size="sm"
            disabled={!batchInput.trim() || isBatchAdding}
            onClick={onBatchAdd}
          >
            {isBatchAdding ? (
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {isBatchAdding ? '添加中...' : '确认添加'}
          </Button>
        </div>

        {displayStocks.length > 0 ? (
          <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="text-sm font-semibold text-slate-800">从当前分组移出股票</h3>
            <div className="max-h-[180px] space-y-1 overflow-y-auto">
              {displayStocks.map((stock) => (
                <button
                  key={stock.code}
                  type="button"
                  onClick={() => onToggleSelect(stock.code)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 font-mono text-xs transition',
                    selectedCodes.has(stock.code)
                      ? 'border-red-300 bg-red-50 text-red-700'
                      : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-200',
                  )}
                >
                  {stock.code}
                  <span className="text-slate-400">{stock.marketLabel}</span>
                </button>
              ))}
            </div>
            <Button
              variant="danger-subtle"
              size="sm"
              disabled={selectedCodes.size === 0 || isBatchRemoving}
              onClick={onBatchRemove}
            >
              {isBatchRemoving ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-red-200 border-t-red-500" />
              ) : null}
              {isBatchRemoving ? '移出中...' : `移出选中 (${selectedCodes.size})`}
            </Button>
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}
