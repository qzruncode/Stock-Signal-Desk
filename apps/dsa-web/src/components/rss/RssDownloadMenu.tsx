import React, { useState } from 'react';
import { Download, ChevronDown } from 'lucide-react';
import type { FeedSpec, RssFeedFormat } from '../../api/rss';
import { rssApi } from '../../api/rss';
import { cn } from '../../utils/cn';

export interface RssDownloadMenuProps {
  spec: FeedSpec | null;
}

const FORMATS: { value: RssFeedFormat; label: string; ext: string }[] = [
  { value: 'rss', label: 'RSS 2.0', ext: 'xml' },
  { value: 'atom', label: 'Atom', ext: 'xml' },
  { value: 'json', label: 'JSON Feed', ext: 'json' },
  { value: 'rss3', label: 'RSS3', ext: 'json' },
];

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export const RssDownloadMenu: React.FC<RssDownloadMenuProps> = ({ spec }) => {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<RssFeedFormat | null>(null);

  const handleDownload = async (fmt: RssFeedFormat, ext: string) => {
    if (!spec) return;
    setBusy(fmt);
    try {
      const blob = await rssApi.getRawFeed({
        route_path: spec.route_path,
        params: spec.params,
        options: spec.options,
        namespace: spec.namespace,
        format: fmt,
        limit: (spec.options.limit as number) || 30,
      });
      const safeName = (spec.route_path.replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '') || 'feed').slice(0, 40);
      triggerDownload(blob, `${safeName}.${ext}`);
    } catch (err) {
      window.alert(`下载失败：${(err as Error).message}`);
    } finally {
      setBusy(null);
      setOpen(false);
    }
  };

  if (!spec) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={Boolean(busy)}
        className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-secondary-text transition hover:border-cyan/40 hover:text-cyan disabled:opacity-50"
      >
        <Download className="h-3.5 w-3.5" />
        下载
        <ChevronDown className={cn('h-3 w-3 transition', open && 'rotate-180')} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-36 overflow-hidden rounded-lg border border-border bg-card shadow-soft-card">
            {FORMATS.map((f) => (
              <button
                key={f.value}
                type="button"
                onClick={() => void handleDownload(f.value, f.ext)}
                disabled={Boolean(busy)}
                className="block w-full px-3 py-2 text-left text-xs text-secondary-text transition hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                {busy === f.value ? `${f.label}…` : f.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
};
