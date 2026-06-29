import type { ReactNode } from 'react';
import {
  Bell,
  BellOff,
  RadioTower,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import { StockAutocomplete } from '../StockAutocomplete';
import type { PromptTemplateItem } from '../../api/prompts';

interface HomeSidebarProps {
  query: string;
  onQueryChange: (value: string) => void;
  onSubmitAnalysis: (
    stockCode?: string,
    stockName?: string,
    selectionSource?: 'manual' | 'autocomplete' | 'import',
  ) => void;
  isAnalyzing: boolean;
  inputError?: string | null;
  templates: PromptTemplateItem[];
  selectedTemplateId: string;
  onTemplateChange: (value: string) => void;
  selectedTemplateName: string;
  onOpenTemplateManager: () => void;
  notify: boolean;
  onNotifyChange: (value: boolean) => void;
  sidebarContent: ReactNode;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

function AnalyzeControl({
  query,
  onQueryChange,
  onSubmitAnalysis,
  isAnalyzing,
  inputError,
  templates,
  selectedTemplateId,
  onTemplateChange,
  selectedTemplateName,
  onOpenTemplateManager,
  notify,
  onNotifyChange,
}: Omit<HomeSidebarProps, 'sidebarContent' | 'mobileOpen' | 'onCloseMobile'>) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white/88 p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-600">
          <RadioTower className="h-3.5 w-3.5 text-cyan-600" />
          实时任务
        </span>
      </div>

      <StockAutocomplete
        value={query}
        onChange={onQueryChange}
        onSubmit={onSubmitAnalysis}
        placeholder="输入股票代码或名称，如 600519、贵州茅台、AAPL"
        disabled={isAnalyzing}
        className={inputError ? 'border-danger/50' : undefined}
        showSuggestionsOnFocus
      />

      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] gap-2">
        {templates.length > 0 ? (
          <select
            value={selectedTemplateId}
            onChange={(event) => onTemplateChange(event.target.value)}
            className="h-10 min-w-0 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition hover:border-cyan-300 focus:border-cyan-500 focus:ring-4 focus:ring-cyan-100"
            aria-label="分析模板"
          >
            {templates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
        ) : (
          <div className="flex h-10 items-center rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-400">加载模板</div>
        )}
        <button
          type="button"
          onClick={onOpenTemplateManager}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-500 transition hover:border-cyan-300 hover:text-cyan-700"
          aria-label="管理分析模板"
        >
          <SlidersHorizontal className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-3 flex items-center justify-between rounded-xl border border-dashed border-slate-300 bg-white/70 px-3 py-2 text-xs text-slate-500">
        <span>
          当前模板：<span className="font-medium text-slate-700">{selectedTemplateName}</span>
        </span>
        <label className="inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-cyan-300">
          <input
            type="checkbox"
            checked={notify}
            onChange={(event) => onNotifyChange(event.target.checked)}
            className="sr-only"
          />
          {notify ? <Bell className="h-4 w-4 text-emerald-600" /> : <BellOff className="h-4 w-4 text-slate-400" />}
          {notify ? '通知开' : '通知关'}
        </label>
      </div>

      <button
        type="button"
        onClick={() => onSubmitAnalysis()}
        disabled={!query || isAnalyzing}
        className="mt-3 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-semibold text-white shadow-[0_10px_24px_rgba(15,23,42,0.22)] transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
      >
        {isAnalyzing ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
        {isAnalyzing ? '分析中' : '分析'}
      </button>
    </div>
  );
}

export default function HomeSidebar(props: HomeSidebarProps) {
  return (
    <>
      <aside className="hidden min-h-0 border-r border-slate-200 bg-white/82 p-4 backdrop-blur-xl lg:flex lg:flex-col">
        <div className="mb-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">Signal Desk</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">选股通知工作台</h1>
          <p className="mt-2 text-sm text-slate-500">搜索股票，选择模板，追踪 AI 完整输入与输出。</p>
        </div>

        <AnalyzeControl {...props} />

        <div className="mt-3 flex min-h-0 flex-1 flex-col">
          {props.sidebarContent}
        </div>
      </aside>

      {props.mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden" onClick={props.onCloseMobile}>
          <div className="page-drawer-overlay absolute inset-0" />
          <div
            className="dashboard-card absolute bottom-0 left-0 top-0 flex w-[min(21rem,86vw)] flex-col overflow-hidden !rounded-none !rounded-r-2xl p-3 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-text">Signal Desk</p>
                <h2 className="text-base font-semibold text-foreground">历史记录</h2>
              </div>
              <button
                type="button"
                onClick={props.onCloseMobile}
                className="rounded-lg px-2 py-1 text-sm text-secondary-text hover:bg-hover"
              >
                关闭
              </button>
            </div>
            {props.sidebarContent}
          </div>
        </div>
      ) : null}
    </>
  );
}
