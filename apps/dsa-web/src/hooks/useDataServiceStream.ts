import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import ReconnectingEventSource from 'reconnecting-eventsource';
import { dataMaintenanceApi, type DataServiceChange, type DatasetOverview, type JobDetail, type SyncJob } from '../api/dataMaintenance';

type JobPage = { items: SyncJob[]; total: number };
export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'failed';

/** One EventSource subscription per mounted workbench; no query polling or custom SSE parser. */
export function useDataServiceStream(seedCursor: string | undefined, enabled: boolean) {
  const cache = useQueryClient();
  const cursor = useRef('0-0');
  const seeded = useRef(false);
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const reconnect = useCallback(() => setAttempt(value => value + 1), []);

  useEffect(() => {
    if (!seeded.current && seedCursor !== undefined) {
      cursor.current = seedCursor;
      seeded.current = true;
    }
  }, [seedCursor]);

  useEffect(() => {
    // This clock only ages heartbeat badges; it never issues a network request.
    const clock = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let networkInterrupted = false;
    const seenJobs = new Set<string>();
    const stream = new ReconnectingEventSource(dataMaintenanceApi.eventsUrl(cursor.current), {
      withCredentials: true, max_retry_time: 10000,
    });
    const offline = () => {
      if (disposed) return;
      networkInterrupted = true;
      stream.close();
      setStatus('reconnecting');
    };
    const online = () => { if (!disposed && networkInterrupted) reconnect(); };
    window.addEventListener('offline', offline);
    window.addEventListener('online', online);
    if (!navigator.onLine) queueMicrotask(offline);
    stream.addEventListener('ready', () => {
      if (disposed || networkInterrupted) return;
      setStatus('live');
      setNow(Date.now());
      const state = cache.getQueryState(['data-service', 'overview']);
      if (!state?.data || state.status === 'error') void cache.invalidateQueries({ queryKey: ['data-service'] });
    });
    stream.addEventListener('reset', (raw: MessageEvent<string>) => {
      if (disposed || networkInterrupted) return;
      cursor.current = raw.lastEventId || '0-0';
      // Reset the version guard too: a restored stream can have a lower cursor.
      // TanStack Query resets and refetches the active snapshot as one lifecycle.
      void cache.resetQueries({ queryKey: ['data-service', 'overview'], exact: true });
      void cache.invalidateQueries({ predicate: query => query.queryKey[0] === 'data-service' && query.queryKey[1] !== 'overview' });
    });
    stream.addEventListener('change', (raw: MessageEvent<string>) => {
      if (disposed || networkInterrupted) return;
      let change: DataServiceChange;
      try {
        change = JSON.parse(raw.data) as DataServiceChange;
        if (!Array.isArray(change.datasets) || !Array.isArray(change.jobs) || !change.coverage || !change.service) throw new Error('Invalid event');
      } catch {
        setStatus('failed');
        // Do not spin on a malformed replay forever; explicitly resynchronize
        // and leave recovery to the user's reconnect action/native transport.
        stream.close();
        void cache.invalidateQueries({ queryKey: ['data-service'] });
        return;
      }
      cursor.current = raw.lastEventId || cursor.current;
      setNow(Date.now());
      setStatus('live');
      cache.setQueryData<DatasetOverview>(['data-service', 'overview'], previous => previous ? {
        ...previous, event_cursor: cursor.current, service: change.service,
        checked_at: change.datasets.length ? change.checked_at : previous.checked_at,
        items: previous.items.map(item => change.datasets.find(next => next.id === item.id) || item),
      } : previous);

      const updates = new Map(change.jobs.map(job => [job.id, job]));
      const unseen = change.jobs.filter(job => !seenJobs.has(job.id));
      change.jobs.forEach(job => seenJobs.add(job.id));
      for (const [key, page] of cache.getQueriesData<JobPage>({ queryKey: ['data-service', 'jobs'] })) {
        if (!page) continue;
        const relevant = unseen.filter(job => (key[2] === 'all' || key[2] === job.dataset) && !page.items.some(item => item.id === job.id));
        if (relevant.length) void cache.invalidateQueries({ queryKey: key, exact: true });
        cache.setQueryData<JobPage>(key, previous => previous ? { ...previous, items: previous.items.map(job => updates.get(job.id) || job) } : previous);
      }
      for (const [key, detail] of cache.getQueriesData<JobDetail>({ queryKey: ['data-service', 'job'] })) {
        const job = updates.get(String(key[2]));
        if (!job || !detail) continue;
        cache.setQueryData<JobDetail>(key, { ...detail, ...job });
        if (job.progress !== detail.progress || job.status !== detail.status) void cache.invalidateQueries({ queryKey: key, exact: true });
      }
      for (const dataset of Object.keys(change.coverage)) {
        void cache.invalidateQueries({ queryKey: ['data-service', 'coverage', dataset] });
      }
    });
    stream.onerror = () => { if (!disposed) setStatus('reconnecting'); };
    // Native EventSource handles normal transport recovery; the library also
    // restores terminal HTTP 5xx failures with jitter and its lastEventId cursor.
    // No application reconnect timer or status-query polling is needed.
    return () => {
      disposed = true;
      window.removeEventListener('offline', offline);
      window.removeEventListener('online', online);
      stream.close();
    };
  }, [cache, enabled, attempt, reconnect]);

  return { status, reconnect, now };
}
