import { useState, useRef, useCallback, useEffect } from 'react';
import { API_BASE_URL } from '../utils/constants';
import type { BusinessResponse } from '../api/business';

export type BusinessStreamPhase = 'idle' | 'connecting' | 'fetching' | 'analyzing' | 'done' | 'error';

export interface BusinessProgressEvent {
  step: number;
  total: number;
  label: string;
  done: boolean;
}

export interface UseBusinessStreamResult {
  phase: BusinessStreamPhase;
  progressEvents: BusinessProgressEvent[];
  streamingText: string;
  envStreamingText: string;
  business: BusinessResponse | null;
  isCached: boolean;
  error: string | null;
  startStream: (symbol: string, force?: boolean) => void;
  abort: () => void;
}

export function useBusinessStream(): UseBusinessStreamResult {
  const [phase, setPhase] = useState<BusinessStreamPhase>('idle');
  const [progressEvents, setProgressEvents] = useState<BusinessProgressEvent[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const [envStreamingText, setEnvStreamingText] = useState('');
  const [business, setBusiness] = useState<BusinessResponse | null>(null);
  const [isCached, setIsCached] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);

  const abort = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  }, []);

  const startStream = useCallback((symbol: string, force: boolean = false) => {
    abort();

    setPhase('connecting');
    setProgressEvents([]);
    setStreamingText('');
    setEnvStreamingText('');
    setBusiness(null);
    setIsCached(false);
    setError(null);

    const url = `${API_BASE_URL}/api/v1/stocks/business/stream?symbol=${encodeURIComponent(symbol)}&force=${force}`;
    const es = new EventSource(url, { withCredentials: true });
    eventSourceRef.current = es;

    es.addEventListener('connected', (e) => {
      const data = JSON.parse(e.data);
      setIsCached(!!data.cached);
      setPhase(data.cached ? 'analyzing' : 'fetching');
    });

    es.addEventListener('progress', (e) => {
      const data: BusinessProgressEvent = JSON.parse(e.data);
      setProgressEvents(prev => [...prev, data]);
      if (data.done) {
        setPhase('analyzing');
      }
    });

    es.addEventListener('analysis_start', () => {
      setPhase('analyzing');
    });

    es.addEventListener('analysis_chunk', (e) => {
      const data = JSON.parse(e.data);
      setStreamingText(prev => prev + data.text);
    });

    es.addEventListener('analysis_done', (e) => {
      const data: BusinessResponse = JSON.parse(e.data);
      setBusiness(data);
      // Don't set phase to done yet — environment analysis may follow
    });

    es.addEventListener('env_analysis_start', () => {
      // Environment analysis phase started
    });

    es.addEventListener('env_analysis_chunk', (e) => {
      const data = JSON.parse(e.data);
      setEnvStreamingText(prev => prev + data.text);
    });

    es.addEventListener('env_analysis_done', (e) => {
      const data: BusinessResponse = JSON.parse(e.data);
      setBusiness(data);
      setPhase('done');
      es.close();
    });

    es.addEventListener('error', (e) => {
      const msgEvent = e as MessageEvent;
      if (msgEvent.data) {
        try {
          const data = JSON.parse(msgEvent.data);
          setError(data.message || '分析失败');
        } catch {
          setError('分析失败');
        }
      } else {
        setError('连接错误');
      }
      setPhase('error');
      es.close();
    });

    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) return;
      setError('连接中断');
      setPhase('error');
      es.close();
    };
  }, [abort]);

  useEffect(() => () => abort(), [abort]);

  return { phase, progressEvents, streamingText, envStreamingText, business, isCached, error, startStream, abort };
}
