import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
    // Keep the full jsdom suite from starving settings views of CPU while
    // preserving parallelism between two independent workers.
    maxWorkers: 2,
    // A few settings flows perform several mocked async UI transitions and
    // need more than ten seconds when the full jsdom suite shares two workers.
    testTimeout: 20_000,
  },
});
