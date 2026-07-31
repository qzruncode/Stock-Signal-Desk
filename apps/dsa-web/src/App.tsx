import type React from 'react';
import { Suspense, useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ApiErrorAlert, Shell } from './components/common';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { scheduleIdlePreload } from './utils/routePreload';
import {
  ChatHomePage,
  NotFoundPage,
  RunExplorerPage,
  SettingPage,
} from './utils/routePreload';
import './App.css';

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
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
          <Route path="/batch/runs/:runId" element={<Navigate to="/" replace />} />
          <Route path="/stocks" element={<Navigate to="/" replace />} />
          <Route path="/portfolio" element={<Navigate to="/" replace />} />
          <Route path="/infos" element={<Navigate to="/" replace />} />
          <Route path="/tools" element={<Navigate to="/setting?tab=tools" replace />} />
          <Route path="/workflows" element={<Navigate to="/" replace />} />
          <Route path="/setting" element={<SettingPage />} />
          <Route path="/runs" element={<RunExplorerPage />} />
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
