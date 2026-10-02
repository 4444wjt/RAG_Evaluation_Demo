import { existsSync } from 'node:fs';
import { defineConfig } from '@playwright/test';

const localChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const localEdge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const browserPath = process.env.PHASE5_BROWSER_PATH
  ?? (existsSync(localChrome) ? localChrome : existsSync(localEdge) ? localEdge : undefined);

export default defineConfig({
  testDir: './browser-tests',
  outputDir: './test-results/browser',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    browserName: 'chromium',
    baseURL: 'http://127.0.0.1:5173',
    launchOptions: { executablePath: browserPath },
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
