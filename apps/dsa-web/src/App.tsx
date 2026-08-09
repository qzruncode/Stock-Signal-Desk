import type React from 'react';
import { Suspense, useEffect } from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import { Shell } from './components/common';
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
  const { isLoading, loadError } = useAuth();

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
