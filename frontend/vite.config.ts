import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

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
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  test: {
    include: ['src/test/**/*.test.ts', 'src/test/**/*.test.tsx'],
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
  },
});
