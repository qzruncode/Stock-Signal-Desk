import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { renderReportParagraphs } from '../../utils/marketMainlineRender';

interface RawOutputPanelProps {
  formattedDisplayText: string;
  streamKind: 'json' | 'report';
}

export const RawOutputPanel: React.FC<RawOutputPanelProps> = ({ formattedDisplayText, streamKind }) => {
  const [showRawOutput, setShowRawOutput] = useState(false);

  const streamContent = streamKind === 'json'
    ? <pre className="market-stream-pre">{formattedDisplayText}</pre>
    : renderReportParagraphs(formattedDisplayText);

  return (
    <div className="market-mainline-muted-block">
      <button
        type="button"
        className="flex w-full items-center justify-between text-left"
        onClick={() => setShowRawOutput((value) => !value)}
      >
        <div>
          <p className="text-sm font-semibold text-slate-900">查看原始流输出</p>
          <p className="mt-1 text-xs text-slate-500">保留模型原始返回，便于核对结构化渲染</p>
        </div>
        <ChevronDown className={`h-4 w-4 text-slate-500 transition-transform ${showRawOutput ? 'rotate-180' : ''}`} />
      </button>
      {showRawOutput && (
        <div className="mt-4">
          <div className={`market-stream-panel ${streamKind === 'json' ? 'market-stream-json' : 'market-stream-report'}`}>
            {streamContent}
          </div>
        </div>
      )}
    </div>
  );
};