import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

vi.mock('../config', async (importOriginal) => ({ ...await importOriginal<typeof import('../config')>(), READ_ONLY: true, STATIC_SITE: true }));
vi.mock('../components/Charts', () => ({
  AbilityChart: () => <div aria-label="能力图测试替身"/>,
  RiskChart: () => <div aria-label="风险图测试替身"/>,
  DurationChart: () => <div aria-label="耗时图测试替身"/>,
}));

let requests: ReturnType<typeof vi.fn>;
beforeEach(() => {
  requests = vi.fn().mockRejectedValue(new Error('a static site has no API'));
  vi.stubGlobal('fetch', requests);
});

describe('GitHub Pages static build', () => {
  it('never probes an API and still serves the frozen demo and the gate audit', async () => {
    render(<App/>);
    expect(screen.getByText('静态演示站 · 无后端')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新检查服务连接' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    expect(screen.getByRole('region', { name: '当前模型质量门禁' })).toHaveTextContent('PASS');
    const nav = screen.getByRole('navigation', { name: '工作区导航' });
    await userEvent.click(within(nav).getByRole('button', { name: '门禁审计' }));
    expect(screen.getByRole('region', { name: '门禁可信度四问' })).toBeVisible();
    expect(requests).not.toHaveBeenCalled();
  });
});
