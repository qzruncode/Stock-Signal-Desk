import React from 'react';
import { Select } from '../common';
import SectorFlowSection from '../SectorFlowSection';
import type { SectorFlowResponse } from '../../types/macro';

interface SectorFlowPanelContentProps {
  sectorFlowData: SectorFlowResponse | null;
  sectorFlowLoading: boolean;
  sectorFlowError: string | null;
  sectorFlowType: string;
  setSectorFlowType: (value: string) => void;
}

const SectorFlowPanelContent: React.FC<SectorFlowPanelContentProps> = ({
  sectorFlowData, sectorFlowLoading, sectorFlowError,
  sectorFlowType, setSectorFlowType,
}) => (
  <div className="space-y-4">
    <div className="flex items-center gap-2">
      <div className="w-32 shrink-0">
        <Select
          value={sectorFlowType}
          onChange={(v) => setSectorFlowType(v)}
          options={[
            { value: 'industry', label: '行业板块' },
            { value: 'concept', label: '概念板块' },
          ]}
        />
      </div>
    </div>
    <SectorFlowSection data={sectorFlowData} loading={sectorFlowLoading} error={sectorFlowError} />
  </div>
);

export default SectorFlowPanelContent;
