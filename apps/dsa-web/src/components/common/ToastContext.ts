import { createContext, useContext } from 'react';
import type React from 'react';
import type { ToastVariant } from '../ui/toast';

export interface ToastInput {
  title: string;
  description?: React.ReactNode;
  variant?: ToastVariant;
  duration?: number;
}

export interface ToastContextValue {
  toast: (input: ToastInput) => string;
  dismissToast: (id: string) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within AppToastProvider');
  }
  return context;
}
