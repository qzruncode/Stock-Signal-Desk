import React, { useState } from 'react';
import { Select } from '../components/common';
import { useMacroData, type MacroDimension } from '../hooks/useMacroData';
import IndexPanelContent from '../components/macroData/IndexPanelContent';
import BondPanelContent from '../components/macroData/BondPanelContent';
import IndicatorPanelContent from '../components/macroData/IndicatorPanelContent';
import SectorFlowPanelContent from '../components/macroData/SectorFlowPanelContent';
import MarketBreadthPanelContent from '../components/macroData/MarketBreadthPanelContent';

const DIMENSION_OPTIONS: { value: MacroDimension; label: string }[] = [
  { value: 'index', label: '大盘指数' },
  { value: 'bond', label: '债券收益率' },
  { value: 'indicator', label: '宏观经济指标' },
  { value: 'sector_flow', label: '板块资金流向' },
  { value: 'market_breadth', label: '市场宽度' },
];

const DEFAULT_INDEX = '000001';
const DEFAULT_DAYS = 20;
const DEFAULT_BOND_COUNTRY = 'cn';
const DEFAULT_BOND_TERM = '10y';
const DEFAULT_INDICATOR_MONTHS = 12;

const MacroDataPage: React.FC = () => {
  const [dimension, setDimension] = useState<MacroDimension>('index');

  // Config state passed to the hook
  const [indexCode, setIndexCode] = useState(DEFAULT_INDEX);
  const [days, setDays] = useState(DEFAULT_DAYS);
  const [bondCountry, setBondCountry] = useState(DEFAULT_BOND_COUNTRY);
  const [bondTerm, setBondTerm] = useState(DEFAULT_BOND_TERM);
  const [indicatorMonths] = useState(DEFAULT_INDICATOR_MONTHS);
  const [sectorFlowType, setSectorFlowType] = useState('industry');

  const {
    indexData, indexLoading, indexError,
    bondData, bondLoading, bondError,
    indicators, indicatorsLoading, indicatorsError,
    sectorFlowData, sectorFlowLoading, sectorFlowError,
    marketBreadthData, marketBreadthLoading, marketBreadthError,
  } = useMacroData({
    dimension, indexCode, days, bondCountry, bondTerm, indicatorMonths, sectorFlowType,
  });

  const renderContent = () => {
    switch (dimension) {
      case 'index':
        return (
          <IndexPanelContent
            indexData={indexData} loading={indexLoading} error={indexError}
            indexCode={indexCode} setIndexCode={setIndexCode}
            days={days} setDays={setDays}
          />
        );
      case 'bond':
        return (
          <BondPanelContent
            bondData={bondData} bondLoading={bondLoading} bondError={bondError}
            bondCountry={bondCountry} setBondCountry={setBondCountry}
            bondTerm={bondTerm} setBondTerm={setBondTerm}
          />
        );
      case 'indicator':
        return (
          <IndicatorPanelContent
            indicators={indicators} loading={indicatorsLoading} error={indicatorsError}
          />
        );
      case 'sector_flow':
        return (
          <SectorFlowPanelContent
            sectorFlowData={sectorFlowData} sectorFlowLoading={sectorFlowLoading} sectorFlowError={sectorFlowError}
            sectorFlowType={sectorFlowType} setSectorFlowType={setSectorFlowType}
          />
        );
      case 'market_breadth':
        return (
          <MarketBreadthPanelContent
            marketBreadthData={marketBreadthData} marketBreadthLoading={marketBreadthLoading} marketBreadthError={marketBreadthError}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex h-[calc(100vh-2rem)] w-full flex-col gap-4 overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Macro Data</p>
          <h1 className="text-2xl font-semibold text-slate-950">宏观数据</h1>
          <p className="mt-0.5 text-sm text-slate-500">大盘指数、宏观经济指标一览</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-40 shrink-0">
            <Select
              value={dimension}
              onChange={(v) => setDimension(v as MacroDimension)}
              options={DIMENSION_OPTIONS}
            />
          </div>
        </div>
      </div>

      {/* Main content */}
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
        {renderContent()}
      </main>
    </div>
  );
};

export default MacroDataPage;