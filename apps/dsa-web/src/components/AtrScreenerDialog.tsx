import React, { useCallback, useState } from 'react';
import { Zap, Loader2, XCircle } from 'lucide-react';
import { useAtrScreener } from '../hooks/useAtrScreener';
import { upsertWatchlistGroup } from '../utils/watchlistGroups';
import { cn } from '../utils/cn';
import { Modal } from './common/Modal';
import { Button } from './common/Button';
import { classifyStock, MARKET_COLORS } from '../utils/market';

interface AtrScreenerDialogProps {
  onError: (msg: string) => void;
  onSuccess: (msg: string) => void;
  onGroupSelect: (groupId: string) => void;
  isOpen: boolean;
  onClose: () => void;
}

const SCREEN_GROUP_NAME = '高波动股';

const AtrScreenerDialog: React.FC<AtrScreenerDialogProps> = ({
  onError,
  onSuccess,
  onGroupSelect,
  isOpen,
  onClose,
}) => {
  const {
    phase,
    progress,
    log,
    result,
    totalRef,
    handleScreen,
    handleCancel,
    handleReset: hookReset,
  } = useAtrScreener({ onError });

  const [groupName, setGroupName] = useState(SCREEN_GROUP_NAME);
  const [isSaving, setIsSaving] = useState(false);

  const handleCreateGroup = useCallback(async () => {
    if (!result || isSaving) return;
    const name = groupName.trim() || SCREEN_GROUP_NAME;
    const matchedCodes = result.matchedCodes;
    setIsSaving(true);
    try {
      const group = await upsertWatchlistGroup(name, matchedCodes, 'screener');
      onGroupSelect(group.id);
      onSuccess(`已保存分组「${name}」，${matchedCodes.length} 只股票`);
      onClose();
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : '保存分组失败');
    } finally {
      setIsSaving(false);
    }
  }, [result, isSaving, groupName, onGroupSelect, onSuccess, onError, onClose]);

  const handleReset = useCallback(() => {
    hookReset();
    setGroupName(SCREEN_GROUP_NAME);
  }, [hookReset]);

  // ─── Render ───

  const renderIdle = () => (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <Zap className="h-5 w-5 text-orange-500 mt-0.5 shrink-0" />
        <div className="text-sm">
          <p className="text-slate-700 font-medium">Phase 1+2: ATR 相对波动率</p>
          <p className="text-slate-700">
            筛选条件：250 日内 ATR% {'>'} 2.8 的天数占比 ≥ 60%
          </p>
          <p className="text-slate-700 font-medium mt-2">Phase 3: 基本面 2 筛（7项）</p>
          <ul className="text-xs text-slate-500 mt-1 space-y-0.5">
            <li>• 营收TTM {'>'} 5亿</li>
            <li>• 扣非净利润TTM {'>'} 0</li>
            <li>• 资产负债率 {'<'} 70%</li>
          </ul>
        </div>
      </div>
      <Button variant="home-action-ai" onClick={handleScreen} className="w-full">
        <Zap className="h-4 w-4" />
        开始筛选
      </Button>
    </div>
  );

  const renderScreening = () => {
    if (!progress) return null;
    const pct = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0;

    return (
      <div className="space-y-4">
        <div className="text-sm space-y-1">
          {progress.stage === 'fetching_list' ? (
            <p className="text-slate-600">
              正在获取股票列表... 第 {progress.currentPage}/{progress.totalPages} 页
            </p>
          ) : progress.stage === 'fundamental_filtering' ? (
            <>
              <p className="text-slate-600">
                基本面筛选中... {progress.fundamentalProcessed ?? 0}/{progress.fundamentalTotal ?? 0} 只
              </p>
            </>
          ) : (
            <>
              <p className="text-slate-600">
                已处理 {progress.processed}/{progress.total} 只 · {progress.matched} 只符合条件
              </p>
              <p className="text-xs text-slate-400">
                预计约 {progress.etaMinutes} 分钟 · 跳过 {progress.skipped} 只
              </p>
            </>
          )}
        </div>

        <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-linear-to-r from-cyan-500 to-indigo-500 rounded-full transition-all duration-300"
            style={{
              width: progress.stage === 'fundamental_filtering'
                ? `${progress.fundamentalTotal && progress.fundamentalTotal > 0
                    ? Math.round(((progress.fundamentalProcessed ?? 0) / progress.fundamentalTotal) * 100)
                    : 0}%`
                : `${pct}%`,
            }}
          />
        </div>

        {log.length > 0 && (
          <div className="max-h-[30vh] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-[11px] leading-relaxed text-slate-600">
            {log.map((entry, i) => (
              <div key={i} className={cn(
                entry.startsWith('✅') ? 'text-emerald-600' :
                entry.startsWith('❌') ? 'text-red-500' :
                entry.startsWith('⚠️') ? 'text-amber-500' :
                entry.startsWith('🆕') ? 'text-orange-500' :
                'text-slate-400',
              )}>
                {entry}
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          onClick={handleCancel}
          className="flex items-center gap-1.5 mx-auto text-xs text-slate-400 hover:text-red-500 transition-colors"
        >
          <XCircle className="h-3.5 w-3.5" />
          取消筛选
        </button>
      </div>
    );
  };

  const renderResult = () => {
    if (!result) return null;
    const displayStocks = result.matchedCodes.map((code) => {
      const { market, marketLabel } = classifyStock(code);
      return { code, market, marketLabel };
    });

    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">共分析 {result.totalAnalyzed} 只，</span>
          <span className="font-medium text-orange-600">{result.matchedCodes.length} 只符合条件</span>
          {result.totalAnalyzed < totalRef.current && (
            <span className="text-xs text-slate-400">
              （ATR 阶段跳过 {totalRef.current - result.totalAnalyzed} 只）
            </span>
          )}
        </div>

        {displayStocks.length > 0 && (
          <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50/50 p-3">
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {displayStocks.map((stock) => (
                <div
                  key={stock.code}
                  className="flex items-center gap-2 rounded-lg border border-slate-100 bg-white px-2.5 py-1.5 text-xs"
                >
                  <span className="font-mono font-medium text-slate-700">{stock.code}</span>
                  <span className={cn(
                    'inline-flex rounded px-1 py-0.5 text-[10px] font-medium',
                    MARKET_COLORS[stock.market] || '',
                  )}>
                    {stock.marketLabel}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3">
          <label className="text-xs font-medium text-slate-600">创建分组</label>
          <div className="flex gap-2">
            <input
              value={groupName}
              onChange={(e) => setGroupName(e.target.value)}
              className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-100"
              placeholder="分组名称"
            />
            <Button
              variant="home-action-ai"
              size="sm"
              onClick={handleCreateGroup}
              disabled={!result.matchedCodes.length || isSaving}
            >
              {isSaving ? '入库中…' : '确定入库'}
            </Button>
          </div>
        </div>

        <button
          type="button"
          onClick={handleReset}
          className="text-xs text-slate-400 hover:text-cyan-600 transition-colors"
        >
          重新筛选
        </button>
      </div>
    );
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="ATR 相对波动率选股"
      width="max-w-xl"
      preventClose={phase === 'screening'}
      footer={
        phase === 'screening' && progress ? (
          <div className="flex items-center gap-2 text-sm text-orange-600">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>
              {progress.stage === 'fetching_list'
                ? `正在获取股票列表... ${progress.currentPage}/${progress.totalPages}`
                : progress.stage === 'fundamental_filtering'
                  ? `基本面筛选中... ${(progress.fundamentalProcessed ?? 0)}/${progress.fundamentalTotal ?? 0} 只`
                  : `已处理 ${progress.processed}/${progress.total} 只，${progress.matched} 只符合`}
            </span>
          </div>
        ) : undefined
      }
    >
      {phase === 'idle' && renderIdle()}
      {phase === 'screening' && renderScreening()}
      {phase === 'done' && renderResult()}
    </Modal>
  );
};

export default AtrScreenerDialog;
