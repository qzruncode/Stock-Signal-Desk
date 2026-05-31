import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { Button } from '../common';

interface BatchScheduleDialogProps {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  times: string[];
  onTimesChange: (times: string[]) => void;
  newTime: string;
  onNewTimeChange: (time: string) => void;
  onAddTime: () => void;
  onSave: () => void;
  onClose: () => void;
}

export default function BatchScheduleDialog({
  enabled,
  onEnabledChange,
  times,
  onTimesChange,
  newTime,
  onNewTimeChange,
  onAddTime,
  onSave,
  onClose,
}: BatchScheduleDialogProps) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div
        className="relative w-full max-w-sm rounded-2xl border border-subtle bg-surface p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">定时跑批设置</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-muted-text hover:bg-hover hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => onEnabledChange(event.target.checked)}
              className="h-3.5 w-3.5 rounded border-border accent-primary"
            />
            启用每日定时跑批
          </label>

          {enabled ? (
            <div className="space-y-2">
              <div className="flex gap-1.5">
                <input
                  type="time"
                  value={newTime}
                  onChange={(event) => onNewTimeChange(event.target.value)}
                  className="flex-1 rounded-lg border border-subtle bg-background px-2 py-1.5 text-xs"
                />
                <Button type="button" variant="secondary" size="sm" onClick={onAddTime}>
                  添加
                </Button>
              </div>
              {times.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {times.map((time) => (
                    <span
                      key={time}
                      className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary"
                    >
                      {time}
                      <button
                        type="button"
                        onClick={() => onTimesChange(times.filter((item) => item !== time))}
                        className="ml-0.5 hover:text-red-500"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-text">尚未添加时间点</p>
              )}
            </div>
          ) : null}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button type="button" variant="primary" size="sm" onClick={onSave}>
            保存
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
