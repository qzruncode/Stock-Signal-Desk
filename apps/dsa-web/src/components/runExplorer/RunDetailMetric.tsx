import type React from 'react';

export function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="rounded-lg bg-muted/60 p-2.5">{icon}<p className="mt-1 text-[11px] text-secondary-text">{label}</p><p className="mt-0.5 text-sm font-medium">{value}</p></div>;
}
