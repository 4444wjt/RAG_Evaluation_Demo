import { act, fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Root } from 'react-dom/client';

const mounted = vi.hoisted(() => ({ roots: [] as Root[] }));
vi.mock('react-dom/client', async (original) => {
  const client = await original<typeof import('react-dom/client')>();
  return { ...client, createRoot: (...args: Parameters<typeof client.createRoot>) => {
    const root = client.createRoot(...args); mounted.roots.push(root); return root;
  } };
});

const request = {
  schemaVersion: 1 as const, currency: 'CNY',
  transactions: [{ transaction_id: 'public-1', timestamp: '2026-11-01T10:00:00Z', customer_id: '模拟客户', terminal_id: 'm1', amount: '10000.00' }],
  rules: { highAmount: '10000.00', windowMinutes: 10, minTransactions: 3 }, claims: [],
};

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv('VITE_FINANCE_PUBLIC', '0');
  vi.stubEnv('VITE_READ_ONLY', '0');
  vi.stubEnv('VITE_STATIC_SITE', '0');
  vi.stubEnv('VITE_BYOK', '0');
  vi.stubEnv('BASE_URL', '/');
  window.history.replaceState(null, '', '/');
});
afterEach(async () => {
  await act(async () => { for (const root of mounted.roots.splice(0)) root.unmount(); });
  document.body.replaceChildren();
  vi.unstubAllEnvs();
});

describe('finance public API paths', () => {
  it.each([
    ['/', '/api/health', '/api/transaction-review/analyze', '/api/transaction-review/replay'],
    ['/finance/', '/finance/api/health', '/finance/api/transaction-review/analyze', '/finance/api/transaction-review/replay'],
  ])('uses the build base %s while preserving the request body', async (base, healthPath, analyzePath, replayPath) => {
    vi.stubEnv('BASE_URL', base);
    const fetcher = vi.fn(async (url: string, _init?: RequestInit) => new Response(JSON.stringify(url.endsWith('/health') ? { status: 'ok' } : {}), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetcher);
    const { checkHealth } = await import('../api/client');
    const { analyzeTransactions, replayTransactions } = await import('../api/transactionReview');
    expect(await checkHealth()).toBe(true);
    await expect(analyzeTransactions(request)).rejects.toThrow(/报告/);
    const bundle = { schema: 'TransactionReviewBundleV1' as const, request, report: {} as never, decisions: [] };
    await expect(replayTransactions(bundle)).rejects.toThrow(/重开标记/);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([healthPath, analyzePath, replayPath]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(request);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual(bundle);
  });
});

describe('finance public entry', () => {
  it('opens the transaction workflow directly without model controls or background API requests', async () => {
    vi.stubEnv('VITE_FINANCE_PUBLIC', '1');
    vi.stubEnv('BASE_URL', '/finance/');
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    document.body.innerHTML = '<div id="root"></div>';
    await act(async () => { await import('../main'); });
    expect(screen.getByRole('heading', { name: '逐笔查清线索的依据' })).toBeVisible();
    expect(screen.getByRole('button', { name: '模拟边界示例' })).toBeEnabled();
    expect(screen.getByLabelText('导入交易 CSV / JSON / 复核包')).toBeEnabled();
    expect(screen.getByText(/服务器内存/)).toHaveTextContent(/脱敏|示例/);
    expect(screen.getByText(/下载.*本机/)).toBeVisible();
    expect(screen.queryByRole('link', { name: '查看演示' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API Key/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '模拟边界示例' }));
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeEnabled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('keeps the normal application entry when finance public mode is absent', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    document.body.innerHTML = '<div id="root"></div>';
    await act(async () => { await import('../main'); });
    expect(screen.getByRole('heading', { level: 1, name: /AI 的每个回答/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: '模拟边界示例' })).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
