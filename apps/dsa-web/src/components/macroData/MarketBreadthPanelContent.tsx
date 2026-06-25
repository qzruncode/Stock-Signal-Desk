import React from 'react';
import MarketBreadthSection from '../MarketBreadthSection';
import type { MarketBreadthResponse } from '../../types/macro';

interface MarketBreadthPanelContentProps {
  marketBreadthData: MarketBreadthResponse | null;
  marketBreadthLoading: boolean;
  marketBreadthError: string | null;
}

const MarketBreadthPanelContent: React.FC<MarketBreadthPanelContentProps> = ({
  marketBreadthData, marketBreadthLoading, marketBreadthError,
}) => (
  <MarketBreadthSection data={marketBreadthData} loading={marketBreadthLoading} error={marketBreadthError} />
);

export default MarketBreadthPanelContent;
