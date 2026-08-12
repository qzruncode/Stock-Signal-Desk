import type React from 'react';
import { Suspense, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import { Shell } from './components/common';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { scheduleIdlePreload } from './utils/routePreload';
import {
  ChatHomePage,
  LoginPage,
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
  const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();

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
      <main className="flex min-h-screen items-center justify-center bg-base px-4 py-10">
        <section className="terminal-card w-full max-w-md rounded-2xl p-6 shadow-soft-card" role="alert">
          <h1 className="text-lg font-semibold text-foreground">无法确认登录状态</h1>
          <p className="mt-2 text-sm text-secondary-text">{loadError.message}</p>
          <button
            type="button"
            onClick={() => void refreshStatus()}
            className="mt-5 inline-flex h-10 items-center justify-center rounded-xl border border-cyan/25 bg-transparent px-4 text-sm font-medium text-cyan transition hover:bg-cyan/10"
          >
            重试
          </button>
        </section>
      </main>
    );
  }

  if (authEnabled && !loggedIn) {
    return (
      <Suspense fallback={<PageFallback />}>
        <LoginPage />
      </Suspense>
    );
  }

  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<ChatHomePage />} />
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
