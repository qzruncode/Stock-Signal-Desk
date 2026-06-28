import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { prettyJson } from '../../utils/marketMainlineFormat';
import type { MarketMainlineReportResponse } from '../../api/marketThemes';

interface RawInputPanelProps {
  debugInput: MarketMainlineReportResponse['debug_input'];
}

export const RawInputPanel: React.FC<RawInputPanelProps> = ({ debugInput }) => {
  const [showRawInput, setShowRawInput] = useState(false);

  return (
    <section className="market-mainline-surface">
      <button
        type="button"
        className="flex w-full items-center justify-between text-left"
        onClick={() => setShowRawInput((value) => !value)}
      >
        <div>
          <p className="text-sm font-semibold text-slate-900">查看模型原始输入</p>
          <p className="mt-1 text-xs text-slate-500">包括 system prompt、user prompt 和 evidence pack</p>
        </div>
        <ChevronDown className={`h-4 w-4 text-slate-500 transition-transform ${showRawInput ? 'rotate-180' : ''}`} />
      </button>

      {showRawInput && (
        <div className="mt-4 space-y-4">
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">System Prompt</p>
            <pre className="overflow-x-auto rounded-[1.25rem] bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
              {debugInput?.system_prompt || '当前还没有 system prompt。'}
            </pre>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">User Prompt</p>
            <pre className="overflow-x-auto rounded-[1.25rem] bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
              {debugInput?.user_prompt || '当前还没有 user prompt。'}
            </pre>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-slate-500">Evidence Pack</p>
            <pre className="overflow-x-auto rounded-[1.25rem] bg-slate-50 p-4 text-xs leading-6 text-slate-700 whitespace-pre-wrap">
              {debugInput?.evidence_pack
                ? prettyJson(debugInput.evidence_pack)
                : '当前还没有 evidence pack。'}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
};