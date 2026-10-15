import react from '@vitejs/plugin-react';
import { searchForWorkspaceRoot } from 'vite';
import { defineConfig } from 'vitest/config';

// The workspace previews the bundled sample texts (../demo.txt, ../scenarios/*.txt) with `?raw`. Dev and build accept
// them as imported source files; Vitest's module runner checks the server allow-list instead, so only test runs widen
// it to the repository (Vite's default deny list for .env and keys still applies).
const fs = process.env.VITEST ? { allow: [searchForWorkspaceRoot(process.cwd()), '..'] } : undefined;

const proxy = {
  '/api': {
    target: 'http://127.0.0.1:8000',
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
  },
});
