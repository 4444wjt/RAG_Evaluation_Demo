import react from '@vitejs/plugin-react';
import { searchForWorkspaceRoot } from 'vite';
import { defineConfig } from 'vitest/config';

// The workspace previews the bundled sample texts (../demo.txt, ../scenarios/*.txt) with `?raw`. Dev and build accept
// them as imported source files; Vitest's module runner checks the server allow-list instead, so only test runs widen
// it to the repository (Vite's default deny list for .env and keys still applies).
const fs = process.env.VITEST ? { allow: [searchForWorkspaceRoot(process.cwd()), '..'] } : undefined;

// Dev-only: point the proxy at another checkout's API (e.g. VITE_API_TARGET=http://127.0.0.1:8010)
// so this worktree can be previewed alongside an API already listening on the default port.
const apiTarget = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000';

const proxy = {
  '/api': {
    target: apiTarget,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api(?=\/|$)/, ''),
    // Evaluation is synchronous and may take several minutes. No short proxy timeout.
    timeout: 0,
    proxyTimeout: 0,
  },
};

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy, fs },
  preview: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  test: {
    include: ['src/test/**/*.test.ts', 'src/test/**/*.test.tsx'],
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
    // Every test file gets its own jsdom, so the worker count is also the number of "browsers"
    // opened at once. Using all cores opened 23 environments and burned 91s of CPU on setup alone
    // (36% of tracked time); the contention starved user-event's per-keypress yields until the
    // slowest case sat at 2.9s against a 5s budget and lost the race intermittently. Half the
    // cores runs the suite faster (33s -> 26s) and leaves real headroom.
    maxWorkers: '50%',
    // Doubled from the 5s default: the slowest case is ~2.9s locally under load, and shared CI
    // runners are several times slower. A timeout there reads as a broken test, not a slow one.
    testTimeout: 10000,
  },
});
