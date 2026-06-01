import type React from 'react';
import { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ApiErrorAlert, Shell } from './components/common';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import './App.css';

const HomePage = lazy(() => import('./pages/HomePage'));
const BatchRunDetailPage = lazy(() => import('./pages/BatchRunDetailPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const MarketStocksPage = lazy(() => import('./pages/MarketStocksPage'));
const WatchlistManagePage = lazy(() => import('./pages/WatchlistManagePage'));
const WorkflowBuilderPage = lazy(() => import('./pages/WorkflowBuilderPage'));
const StockAnalysisPage = lazy(() => import('./pages/StockAnalysisPage'));
const MacroDataPage = lazy(() => import('./pages/MacroDataPage'));
const MarketAnalysisPage = lazy(() => import('./pages/MarketAnalysisPage'));

const PageFallback: React.FC = () => (
  <div className="flex min-h-screen items-center justify-center bg-base">
    <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
  </div>
);

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();

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

  if (authEnabled && !loggedIn) {
    if (location.pathname === '/login') {
      return (
        <Suspense fallback={<PageFallback />}>
          <LoginPage />
        </Suspense>
      );
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/batch/runs/:runId" element={<BatchRunDetailPage />} />
          <Route path="/stocks" element={<MarketStocksPage />} />
          <Route path="/portfolio" element={<WatchlistManagePage />} />
          <Route path="/analysis" element={<StockAnalysisPage />} />
          <Route path="/macro" element={<MacroDataPage />} />
          <Route path="/market" element={<MarketAnalysisPage />} />
          <Route path="/workflows" element={<WorkflowBuilderPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
        <Route path="/login" element={<LoginPage />} />
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
