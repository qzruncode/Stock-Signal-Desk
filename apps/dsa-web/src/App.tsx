/* eslint-disable @typescript-eslint/unbound-method */
import type React from 'react';
import { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ApiErrorAlert, Shell } from './components/common';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import './App.css';

function lazyWithPreload<T extends { default: React.ComponentType }>(factory: () => Promise<T>) {
  const Component = lazy(factory);
  type Preloadable = typeof Component & { preload?: () => Promise<T> };
  (Component as Preloadable).preload = factory;
  return Component as Preloadable;
}

const ChatHomePage = lazyWithPreload(() => import('./pages/ChatHomePage'));
const HomePage = lazyWithPreload(() => import('./pages/HomePage'));
const BatchRunDetailPage = lazyWithPreload(() => import('./pages/BatchRunDetailPage'));
const NotFoundPage = lazyWithPreload(() => import('./pages/NotFoundPage'));
const SettingsPage = lazyWithPreload(() => import('./pages/SettingsPage'));
const MarketStocksPage = lazyWithPreload(() => import('./pages/MarketStocksPage'));
const WatchlistManagePage = lazyWithPreload(() => import('./pages/WatchlistManagePage'));
const WorkflowBuilderPage = lazyWithPreload(() => import('./pages/WorkflowBuilderPage'));
const StockAnalysisPage = lazyWithPreload(() => import('./pages/StockAnalysisPage'));
const MacroDataPage = lazyWithPreload(() => import('./pages/MacroDataPage'));
const MarketAnalysisPage = lazyWithPreload(() => import('./pages/MarketAnalysisPage'));
const MarketLeadersPage = lazyWithPreload(() => import('./pages/MarketLeadersPage'));
const RssPage = lazyWithPreload(() => import('./pages/RssPage'));

const ROUTE_PRELOAD_MAP: Record<string, () => Promise<unknown>> = {
  '/': ChatHomePage.preload!,
  '/dashboard': HomePage.preload!,
  '/stocks': MarketStocksPage.preload!,
  '/portfolio': WatchlistManagePage.preload!,
  '/analysis': StockAnalysisPage.preload!,
  '/rss': RssPage.preload!,
  '/macro': MacroDataPage.preload!,
  '/market': MarketAnalysisPage.preload!,
  '/market-leaders': MarketLeadersPage.preload!,
  '/workflows': WorkflowBuilderPage.preload!,
  '/settings': SettingsPage.preload!,
};

export const preloadRoute = (path: string): void => {
  const preload = ROUTE_PRELOAD_MAP[path];
  if (preload) {
    void preload();
  }
};

const HIGH_FREQUENCY_ROUTES = ['/', '/dashboard', '/analysis', '/stocks'];

const scheduleIdlePreload = (): void => {
  const run = () => {
    HIGH_FREQUENCY_ROUTES.forEach((path) => preloadRoute(path));
  };
  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(run, { timeout: 3000 });
  } else {
    setTimeout(run, 1500);
  }
};

const PageFallback: React.FC = () => (
  <div className="flex min-h-screen items-center justify-center bg-base">
    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
  </div>
);

const AppContent: React.FC = () => {
  const location = useLocation();
  const { isLoading, loadError, refreshStatus } = useAuth();

  useEffect(() => {
    if (!isLoading && !loadError) {
      scheduleIdlePreload();
    }
  }, [isLoading, loadError]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
        <div className="w-full max-w-lg">
          <ApiErrorAlert error={loadError} />
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => void refreshStatus()}
        >
          重试
        </button>
      </div>
    );
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<ChatHomePage />} />
          <Route path="/dashboard" element={<HomePage />} />
          <Route path="/batch/runs/:runId" element={<BatchRunDetailPage />} />
          <Route path="/stocks" element={<MarketStocksPage />} />
          <Route path="/portfolio" element={<WatchlistManagePage />} />
          <Route path="/analysis" element={<StockAnalysisPage />} />
          <Route path="/rss" element={<RssPage />} />
          <Route path="/macro" element={<MacroDataPage />} />
          <Route path="/market" element={<MarketAnalysisPage />} />
          <Route path="/market-leaders" element={<MarketLeadersPage />} />
          <Route path="/workflows" element={<WorkflowBuilderPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <AuthProvider>
        <AppContent />
      </AuthProvider>
    </Router>
  );
};

export default App;
