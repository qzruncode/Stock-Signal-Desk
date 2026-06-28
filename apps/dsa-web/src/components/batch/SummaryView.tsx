import React from 'react';
import { FolderPlus } from 'lucide-react';
import { Button } from '../common';

interface SummaryViewProps {
  summaryMd: string;
  groupName: string;
  defaultGroupName: string;
  passedCodesLength: number;
  isCreatingGroup: boolean;
  onGroupNameChange: (value: string) => void;
  onCreateGroup: () => void;
}

export const SummaryView: React.FC<SummaryViewProps> = ({
  summaryMd,
  groupName,
  defaultGroupName,
  passedCodesLength,
  isCreatingGroup,
  onGroupNameChange,
  onCreateGroup,
}) => (
  <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-subtle bg-surface">
    <div className="border-b border-subtle px-5 py-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-sm font-semibold text-foreground">汇总统计 MD</p>
          <p className="text-xs text-muted-text">通知同源的统计报告，包含整体完成、成功率和失败项。</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <input
            value={groupName}
            onChange={(event) => onGroupNameChange(event.target.value)}
            placeholder={defaultGroupName}
            className="h-9 min-w-[16rem] rounded-lg border border-subtle bg-background px-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={onCreateGroup}
            disabled={passedCodesLength === 0 || isCreatingGroup}
          >
            <FolderPlus className="h-4 w-4" />
            {isCreatingGroup ? '入库中…' : `建股票池 (${passedCodesLength})`}
          </Button>
        </div>
      </div>
    </div>
    <pre className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words px-5 py-4 text-sm leading-7 text-secondary-text">
      {summaryMd || '暂无汇总报告'}
    </pre>
  </section>
);