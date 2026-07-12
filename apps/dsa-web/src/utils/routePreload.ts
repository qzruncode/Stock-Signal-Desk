/**
 * Route-level lazy loading + idle preloading.
 *
 * Extracted from App.tsx so the page module only exports components (Fast
 * Refresh requires component files to export components only). Shell.tsx and
 * App.tsx both consume the lazy page components and the preload helper here.
 */
import type React from 'react';
import { lazy } from 'react';

function lazyWithPreload<T extends { default: React.ComponentType }>(factory: () => Promise<T>) {
  const Component = lazy(factory);
  type Preloadable = typeof Component & { preload?: () => Promise<T> };
  (Component as Preloadable).preload = factory;
  return Component as Preloadable;
}

export const ChatHomePage = lazyWithPreload(() => import('../pages/ChatHomePage'));
export const HomePage = lazyWithPreload(() => import('../pages/HomePage'));
export const BatchRunDetailPage = lazyWithPreload(() => import('../pages/BatchRunDetailPage'));
export const NotFoundPage = lazyWithPreload(() => import('../pages/NotFoundPage'));
export const SettingPage = lazyWithPreload(() => import('../pages/SettingPage'));
export const MarketStocksPage = lazyWithPreload(() => import('../pages/MarketStocksPage'));
export const WatchlistManagePage = lazyWithPreload(() => import('../pages/WatchlistManagePage'));
export const WorkflowBuilderPage = lazyWithPreload(() => import('../pages/WorkflowBuilderPage'));
export const StockAnalysisPage = lazyWithPreload(() => import('../pages/StockAnalysisPage'));
export const MacroDataPage = lazyWithPreload(() => import('../pages/MacroDataPage'));
export const MarketAnalysisPage = lazyWithPreload(() => import('../pages/MarketAnalysisPage'));
export const MarketLeadersPage = lazyWithPreload(() => import('../pages/MarketLeadersPage'));
export const RssPage = lazyWithPreload(() => import('../pages/RssPage'));

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
  '/setting': SettingPage.preload!,
};

export const preloadRoute = (path: string): void => {
  const preload = ROUTE_PRELOAD_MAP[path];
  if (preload) {
    void preload();
  }
};

const HIGH_FREQUENCY_ROUTES = ['/', '/dashboard', '/analysis', '/stocks'];

export const scheduleIdlePreload = (): void => {
  const run = () => {
    HIGH_FREQUENCY_ROUTES.forEach((path) => preloadRoute(path));
  };
  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(run, { timeout: 3000 });
  } else {
    setTimeout(run, 1500);
  }
};
