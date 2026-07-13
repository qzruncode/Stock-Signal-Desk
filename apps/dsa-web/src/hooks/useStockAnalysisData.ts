import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { announcementsApi, type AnnouncementsResponse } from '../api/announcements';
import { financialStatementsApi, type FinancialStatementsResponse } from '../api/financialStatements';
import { newsApi, type NewsResponse } from '../api/news';
import { riskEventsApi, type RiskEventsResponse } from '../api/riskEvents';
import { researchReportApi, type ResearchReportResponse } from '../api/researchReports';
import { sentimentApi, type SentimentResponse } from '../api/sentiment';
import { shareholderApi, type ShareholderStructureResponse } from '../api/shareholder';
import { socialSentimentApi, type SocialSentimentResponse } from '../api/socialSentiment';
import { valuationApi, type ValuationRatiosResponse } from '../api/valuation';
import { klineApi, type KlineResponse } from '../api/kline';

export type AnalysisMode =
  | 'kline'
  | 'financials'
  | 'business'
  | 'valuation'
  | 'industry-cycle'
  | 'shareholder'
  | 'news'
  | 'risk'
  | 'announcements'
  | 'sentiment'
  | 'research'
  | 'social';

export interface StockAnalysisState {
  // Search
  searchValue: string;
  setSearchValue: (v: string) => void;
  selectedSymbol: string | null;
  mode: AnalysisMode;
  setMode: (m: AnalysisMode) => void;
  handleStockSelect: (code: string) => void;

  // K-line
  klineData: KlineResponse | null;
  klineLoading: boolean;
  klineError: string | null;

  // Financial statements
  financialStatements: FinancialStatementsResponse | null;
  financialStatementsLoading: boolean;

  // Valuation & shareholder
  valuation: ValuationRatiosResponse | null;
  valuationLoading: boolean;
  shareholder: ShareholderStructureResponse | null;
  shareholderLoading: boolean;

  // News / risks / announcements
  news: NewsResponse | null;
  newsLoading: boolean;
  riskEvents: RiskEventsResponse | null;
  riskEventsLoading: boolean;
  announcements: AnnouncementsResponse | null;
  announcementsLoading: boolean;

  // Sentiment / research / social
  sentiment: SentimentResponse | null;
  sentimentLoading: boolean;
  research: ResearchReportResponse | null;
  researchLoading: boolean;
  social: SocialSentimentResponse | null;
  socialLoading: boolean;
}

export function useStockAnalysisData(): StockAnalysisState {
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchValue, setSearchValue] = useState('');

  const [klineData, setKlineData] = useState<KlineResponse | null>(null);
  const [klineLoading, setKlineLoading] = useState(false);
  const [klineError, setKlineError] = useState<string | null>(null);

  const [financialStatements, setFinancialStatements] = useState<FinancialStatementsResponse | null>(null);
  const [financialStatementsLoading, setFinancialStatementsLoading] = useState(false);

  const [valuation, setValuation] = useState<ValuationRatiosResponse | null>(null);
  const [valuationLoading, setValuationLoading] = useState(false);
  const [shareholder, setShareholder] = useState<ShareholderStructureResponse | null>(null);
  const [shareholderLoading, setShareholderLoading] = useState(false);

  const [news, setNews] = useState<NewsResponse | null>(null);
  const [newsLoading, setNewsLoading] = useState(false);
  const [riskEvents, setRiskEvents] = useState<RiskEventsResponse | null>(null);
  const [riskEventsLoading, setRiskEventsLoading] = useState(false);
  const [announcements, setAnnouncements] = useState<AnnouncementsResponse | null>(null);
  const [announcementsLoading, setAnnouncementsLoading] = useState(false);

  const [sentiment, setSentiment] = useState<SentimentResponse | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState(false);
  const [research, setResearch] = useState<ResearchReportResponse | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);
  const [social, setSocial] = useState<SocialSentimentResponse | null>(null);
  const [socialLoading, setSocialLoading] = useState(false);

  const [mode, setMode] = useState<AnalysisMode>('kline');

  const selectedSymbol = searchParams.get('symbol');

  const fetchKline = useCallback(async (symbol: string) => {
    setKlineLoading(true);
    setKlineError(null);
    try {
      const result = await klineApi.getKline(symbol);
      setKlineData(result);
    } catch {
      setKlineData(null);
      setKlineError('获取K线数据失败，请稍后重试');
    } finally {
      setKlineLoading(false);
    }
  }, []);

  const fetchFinancialStatements = useCallback(async (symbol: string) => {
    setFinancialStatementsLoading(true);
    try {
      const result = await financialStatementsApi.getStatements(symbol);
      setFinancialStatements(result);
    } catch {
      setFinancialStatements(null);
    } finally {
      setFinancialStatementsLoading(false);
    }
  }, []);

  const fetchValuation = useCallback(async (symbol: string) => {
    setValuationLoading(true);
    try {
      const result = await valuationApi.getValuationRatios(symbol);
      setValuation(result);
    } catch {
      setValuation(null);
    } finally {
      setValuationLoading(false);
    }
  }, []);

  const fetchShareholder = useCallback(async (symbol: string) => {
    setShareholderLoading(true);
    try {
      const result = await shareholderApi.getShareholderStructure(symbol);
      setShareholder(result);
    } catch {
      setShareholder(null);
    } finally {
      setShareholderLoading(false);
    }
  }, []);

  const fetchNews = useCallback(async (symbol: string) => {
    setNewsLoading(true);
    try {
      const result = await newsApi.searchNews(symbol, 90);
      setNews(result);
    } catch {
      setNews(null);
    } finally {
      setNewsLoading(false);
    }
  }, []);

  const fetchRiskEvents = useCallback(async (symbol: string) => {
    setRiskEventsLoading(true);
    try {
      const result = await riskEventsApi.getRiskEvents(symbol);
      setRiskEvents(result);
    } catch {
      setRiskEvents(null);
    } finally {
      setRiskEventsLoading(false);
    }
  }, []);

  const fetchAnnouncements = useCallback(async (symbol: string) => {
    setAnnouncementsLoading(true);
    try {
      const result = await announcementsApi.getAnnouncements(symbol);
      setAnnouncements(result);
    } catch {
      setAnnouncements(null);
    } finally {
      setAnnouncementsLoading(false);
    }
  }, []);

  const fetchSentiment = useCallback(async (symbol: string) => {
    setSentimentLoading(true);
    try {
      const result = await sentimentApi.getSentiment(symbol);
      setSentiment(result);
    } catch {
      setSentiment(null);
    } finally {
      setSentimentLoading(false);
    }
  }, []);

  const fetchResearch = useCallback(async (symbol: string) => {
    setResearchLoading(true);
    try {
      const result = await researchReportApi.getResearchReports(symbol);
      setResearch(result);
    } catch {
      setResearch(null);
    } finally {
      setResearchLoading(false);
    }
  }, []);

  const fetchSocial = useCallback(async (symbol: string) => {
    setSocialLoading(true);
    try {
      const result = await socialSentimentApi.getSocialSentiment(symbol);
      setSocial(result);
    } catch {
      setSocial(null);
    } finally {
      setSocialLoading(false);
    }
  }, []);

  // Initial load from URL
  useEffect(() => {
    const symbol = searchParams.get('symbol');
    if (symbol) {
      setSearchValue(symbol);
      void fetchFinancialStatements(symbol);
      void fetchValuation(symbol);
      void fetchShareholder(symbol);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Lazy-fetch per tab
  useEffect(() => {
    if (mode === 'kline' && selectedSymbol) {
      void fetchKline(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchKline]);

  useEffect(() => {
    if (mode === 'news' && selectedSymbol) {
      void fetchNews(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchNews]);

  useEffect(() => {
    if (mode === 'risk' && selectedSymbol) {
      void fetchRiskEvents(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchRiskEvents]);

  useEffect(() => {
    if (mode === 'announcements' && selectedSymbol) {
      void fetchAnnouncements(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchAnnouncements]);

  useEffect(() => {
    if (mode === 'sentiment' && selectedSymbol) {
      void fetchSentiment(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchSentiment]);

  useEffect(() => {
    if (mode === 'research' && selectedSymbol) {
      void fetchResearch(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchResearch]);

  useEffect(() => {
    if (mode === 'social' && selectedSymbol) {
      void fetchSocial(selectedSymbol);
    }
  }, [mode, selectedSymbol, fetchSocial]);

  const handleStockSelect = useCallback(
    (code: string) => {
      setSearchValue(code);
      setSearchParams({ symbol: code });
      void fetchFinancialStatements(code);
      void fetchValuation(code);
      void fetchShareholder(code);
    },
    [setSearchParams, fetchFinancialStatements, fetchValuation, fetchShareholder],
  );

  return {
    searchValue,
    setSearchValue,
    selectedSymbol,
    mode,
    setMode,
    handleStockSelect,
    klineData,
    klineLoading,
    klineError,
    financialStatements,
    financialStatementsLoading,
    valuation,
    valuationLoading,
    shareholder,
    shareholderLoading,
    news,
    newsLoading,
    riskEvents,
    riskEventsLoading,
    announcements,
    announcementsLoading,
    sentiment,
    sentimentLoading,
    research,
    researchLoading,
    social,
    socialLoading,
  };
}
