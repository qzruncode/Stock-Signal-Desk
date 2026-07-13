import React, { lazy, Suspense } from 'react';
import KLineChartPanel from '../KLineChartPanel';
import FinancialStatementsPanel from '../FinancialStatementsPanel';
import {
  AnnouncementsPanel,
  NewsPanel,
  ResearchPanel,
  RiskEventsPanel,
  SentimentPanel,
  SocialSentimentPanel,
} from '../NewsAnnouncementPanel';
import { BuyCriteriaPanel } from '../buyCriteria/BuyCriteriaPanel';
import { DataSection } from './DataSection';
import { ValuationRatiosPanel, PriceOverdraftPanel } from './ValuationPanels';
import { ShareholderStructurePanel } from './ShareholderStructurePanel';

const BusinessAnalysisPanelStreaming = lazy(() => import('./BusinessAnalysisPanelStreaming'));
import type { AnalysisMode } from '../../hooks/useStockAnalysisData';
import type { KlineResponse } from '../../api/kline';
import type { FinancialStatementsResponse } from '../../api/financialStatements';
import type { ValuationRatiosResponse } from '../../api/valuation';
import type { ShareholderStructureResponse } from '../../api/shareholder';
import type { NewsResponse } from '../../api/news';
import type { RiskEventsResponse } from '../../api/riskEvents';
import type { AnnouncementsResponse } from '../../api/announcements';
import type { SentimentResponse } from '../../api/sentiment';
import type { ResearchReportResponse } from '../../api/researchReports';
import type { SocialSentimentResponse } from '../../api/socialSentiment';

interface StockContentRouterProps {
  mode: AnalysisMode;
  selectedSymbol: string;
  klineData: KlineResponse | null;
  klineLoading: boolean;
  klineError: string | null;
  financialStatements: FinancialStatementsResponse | null;
  financialStatementsLoading: boolean;
  valuation: ValuationRatiosResponse | null;
  valuationLoading: boolean;
  shareholder: ShareholderStructureResponse | null;
  shareholderLoading: boolean;
  news: NewsResponse | null;
  newsLoading: boolean;
  riskEvents: RiskEventsResponse | null;
  riskEventsLoading: boolean;
  announcements: AnnouncementsResponse | null;
  announcementsLoading: boolean;
  sentiment: SentimentResponse | null;
  sentimentLoading: boolean;
  research: ResearchReportResponse | null;
  researchLoading: boolean;
  social: SocialSentimentResponse | null;
  socialLoading: boolean;
}

export const StockContentRouter: React.FC<StockContentRouterProps> = (props) => {
  const { mode, selectedSymbol } = props;

  switch (mode) {
    case 'kline':
      return <KLineChartPanel data={props.klineData} loading={props.klineLoading} error={props.klineError} />;
    case 'financials':
      return (
        <DataSection
          loading={props.financialStatementsLoading}
          loadingLabel="正在获取财报数据..."
          data={props.financialStatements}
          emptyText="暂无财报数据"
          render={(d) => (
            <FinancialStatementsPanel
              balance_sheet={d.balance_sheet}
              income_statement={d.income_statement}
              cashflow={d.cashflow}
            />
          )}
        />
      );
    case 'business':
      return (
        <div className="space-y-6">
          <Suspense fallback={<BusinessLoadingFallback />}>
            <BusinessAnalysisPanelStreaming symbol={selectedSymbol} />
          </Suspense>
        </div>
      );
    case 'valuation':
      return (
        <DataSection
          loading={props.valuationLoading}
          loadingLabel="正在获取估值指标..."
          data={props.valuation}
          emptyText="暂无估值数据"
          render={(d) => (
            <>
              <ValuationRatiosPanel valuation={d} />
              <PriceOverdraftPanel valuation={d} />
            </>
          )}
        />
      );
    case 'industry-cycle':
      return <BuyCriteriaPanel symbol={selectedSymbol} valuation={props.valuation ?? undefined} />;
    case 'news':
      return (
        <DataSection
          loading={props.newsLoading}
          loadingLabel="正在获取相关新闻..."
          data={props.news}
          emptyText="暂无相关新闻"
          render={(d) => <NewsPanel news={d} />}
        />
      );
    case 'announcements':
      return (
        <DataSection
          loading={props.announcementsLoading}
          loadingLabel="正在获取公司公告..."
          data={props.announcements}
          emptyText="暂无公司公告"
          render={(d) => <AnnouncementsPanel announcements={d} />}
        />
      );
    case 'risk':
      return (
        <DataSection
          loading={props.riskEventsLoading}
          loadingLabel="正在扫描风险事件..."
          data={props.riskEvents}
          emptyText="暂无风险事件"
          render={(d) => <RiskEventsPanel riskEvents={d} />}
        />
      );
    case 'sentiment':
      return (
        <DataSection
          loading={props.sentimentLoading}
          loadingLabel="正在分析舆情情绪..."
          data={props.sentiment}
          emptyText="暂无舆情数据"
          render={(d) => <SentimentPanel sentiment={d} />}
        />
      );
    case 'research':
      return (
        <DataSection
          loading={props.researchLoading}
          loadingLabel="正在获取券商研报..."
          data={props.research}
          emptyText="暂无券商研报"
          render={(d) => <ResearchPanel research={d} />}
        />
      );
    case 'social':
      return (
        <DataSection
          loading={props.socialLoading}
          loadingLabel="正在分析社交情绪..."
          data={props.social}
          emptyText="暂无社交情绪数据"
          render={(d) => <SocialSentimentPanel social={d} />}
        />
      );
    case 'shareholder':
    default:
      return (
        <DataSection
          loading={props.shareholderLoading}
          loadingLabel="正在获取股东结构..."
          data={props.shareholder}
          emptyText="暂无股东结构数据"
          render={(d) => <ShareholderStructurePanel shareholder={d} />}
        />
      );
  }
}

function BusinessLoadingFallback() {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-cyan-400 border-t-transparent" />
      正在加载业务分析模块...
    </div>
  );
};