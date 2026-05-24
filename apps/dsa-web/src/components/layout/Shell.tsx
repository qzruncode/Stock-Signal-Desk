import type React from 'react';
import { Outlet } from 'react-router-dom';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  return (
    <div className="min-h-screen bg-background text-foreground selection:bg-primary/20">
      <div className="mx-auto flex min-h-screen w-full max-w-[1640px] px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
        <main className="min-h-0 min-w-0 flex-1 touch-pan-y">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
};
