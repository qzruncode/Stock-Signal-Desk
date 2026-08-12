import type { UseFormReturn } from 'react-hook-form';
import { Button, ConfirmDialog, Modal } from '../common';
import type { WatchlistGroup } from '../../api/watchlist';
import type { WatchlistFormValues } from './StockListPanels';

const COMPACT_BUTTON_CLASS = 'h-7 gap-1 rounded-md px-2 text-[11px]';

interface StockListGroupDialogsProps {
  deleteTarget: WatchlistGroup | null;
  setDeleteTarget: (value: WatchlistGroup | null) => void;
  deleteGroup: (group: WatchlistGroup) => void | Promise<void>;
  createGroupOpen: boolean;
  setCreateGroupOpen: (value: boolean) => void;
  busyAction: string | null;
  watchlistForm: UseFormReturn<WatchlistFormValues>;
  createGroup: (values: WatchlistFormValues) => void | Promise<void>;
  editingGroupId: string | null;
  closeEditGroup: () => void;
  renameGroup: (values: WatchlistFormValues) => void | Promise<void>;
}

function groupName(group: WatchlistGroup | null): string {
  const name = group?.name?.trim();
  return !name || name.toLowerCase() === 'null' || name.toLowerCase() === 'undefined'
    ? '未命名分组'
    : name;
}

export function StockListGroupDialogs({
  deleteTarget,
  setDeleteTarget,
  deleteGroup,
  createGroupOpen,
  setCreateGroupOpen,
  busyAction,
  watchlistForm,
  createGroup,
  editingGroupId,
  closeEditGroup,
  renameGroup,
}: StockListGroupDialogsProps) {
  return (
    <>
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        title="删除分组"
        message={`确定删除分组「${deleteTarget ? groupName(deleteTarget) : ''}」吗？其中的股票不会从默认自选股中删除。`}
        confirmText="删除分组"
        cancelText="取消"
        isDanger
        onConfirm={() => {
          if (!deleteTarget) return;
          void deleteGroup(deleteTarget);
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />

      <Modal
        isOpen={createGroupOpen}
        onClose={() => {
          if (busyAction !== 'create-group') {
            setCreateGroupOpen(false);
            watchlistForm.setValue('newGroupName', '');
            watchlistForm.clearErrors('newGroupName');
          }
        }}
        title="新建分组"
        width="max-w-md"
        preventClose={busyAction === 'create-group'}
        footer={(
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={() => {
                setCreateGroupOpen(false);
                watchlistForm.setValue('newGroupName', '');
                watchlistForm.clearErrors('newGroupName');
              }}
              disabled={busyAction === 'create-group'}
              className={COMPACT_BUTTON_CLASS}
            >
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="button"
              onClick={() => { void createGroup(watchlistForm.getValues()); }}
              isLoading={busyAction === 'create-group'}
              loadingText="保存中..."
              className={COMPACT_BUTTON_CLASS}
            >
              保存
            </Button>
          </div>
        )}
      >
        <form id="create-group-form" onSubmit={watchlistForm.handleSubmit(createGroup)} className="space-y-2">
          <label htmlFor="new-group-name" className="text-[11px] font-medium text-foreground">分组名称</label>
          <input
            {...watchlistForm.register('newGroupName', { required: '请输入分组名称' })}
            id="new-group-name"
            autoFocus
            placeholder="例如：长期持仓"
            className="input-surface h-9 w-full rounded-md border px-2.5 text-xs focus:outline-none"
          />
          {watchlistForm.formState.errors.newGroupName?.message ? (
            <p className="text-xs text-danger">{watchlistForm.formState.errors.newGroupName.message}</p>
          ) : null}
        </form>
      </Modal>

      <Modal
        isOpen={editingGroupId !== null}
        onClose={closeEditGroup}
        title="编辑分组"
        width="max-w-md"
        preventClose={busyAction === 'rename-group'}
        footer={(
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={closeEditGroup}
              disabled={busyAction === 'rename-group'}
              className={COMPACT_BUTTON_CLASS}
            >
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="button"
              onClick={() => { void renameGroup(watchlistForm.getValues()); }}
              isLoading={busyAction === 'rename-group'}
              loadingText="保存中..."
              className={COMPACT_BUTTON_CLASS}
            >
              保存
            </Button>
          </div>
        )}
      >
        <form id="edit-group-form" onSubmit={watchlistForm.handleSubmit(renameGroup)} className="space-y-2">
          <label htmlFor="edit-group-name" className="text-[11px] font-medium text-foreground">分组名称</label>
          <input
            {...watchlistForm.register('renameValue', { required: '请输入分组名称' })}
            id="edit-group-name"
            autoFocus
            className="input-surface h-9 w-full rounded-md border px-2.5 text-xs focus:outline-none"
          />
          {watchlistForm.formState.errors.renameValue?.message ? (
            <p className="text-xs text-danger">{watchlistForm.formState.errors.renameValue.message}</p>
          ) : null}
        </form>
      </Modal>
    </>
  );
}

export default StockListGroupDialogs;
