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
export const NotFoundPage = lazyWithPreload(() => import('../pages/NotFoundPage'));
export const SettingPage = lazyWithPreload(() => import('../pages/SettingPage'));
export const RunExplorerPage = lazyWithPreload(() => import('../pages/RunExplorerPage'));

const ROUTE_PRELOAD_MAP: Record<string, () => Promise<unknown>> = {
  '/': ChatHomePage.preload!,
  '/setting': SettingPage.preload!,
  '/runs': RunExplorerPage.preload!,
};

export const preloadRoute = (path: string): void => {
  const preload = ROUTE_PRELOAD_MAP[path];
  if (preload) {
    void preload();
  }
};

const HIGH_FREQUENCY_ROUTES = ['/'];

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
