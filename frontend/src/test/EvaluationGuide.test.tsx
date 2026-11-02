import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import demo from '../fixtures/demo.json';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', async (original) => ({
  ...await original<typeof import('../config')>(),
  get READ_ONLY() { return mode.readOnly; },
  get STATIC_SITE() { return mode.staticSite; },
}));
vi.mock('../components/Charts', () => ({
  AbilityChart: () => <div/>, RiskChart: () => <div/>, DurationChart: () => <div/>,
}));

const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
let requests: ReturnType<typeof vi.fn>;
beforeEach(() => {
  mode.readOnly = false;
  mode.staticSite = false;
  localStorage.clear();
  localStorage.setItem('veriscope:evaluation-guide', 'done');
  window.history.replaceState({}, '', '/');
  requests = vi.fn((url: string) => Promise.resolve(response(url === '/api/health' ? { status: 'ok' } : url === '/api/models' ? { models: [] } : demo.result)));
  vi.stubGlobal('fetch', requests);
});
afterEach(() => { vi.restoreAllMocks(); });

const guide = () => screen.getByRole('dialog', { name: '评测使用教程' });
const advance = () => userEvent.click(within(guide()).getByRole('button', { name: /下一步|开始了解/ }));
async function finishGuide() {
  for (let index = 0; index < 12; index += 1) await advance();
  await userEvent.click(within(guide()).getByRole('button', { name: '完成教程' }));
}
const writes = () => requests.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET');

describe('evaluation walkthrough', () => {
  it('walks the full flow without uploading, evaluating, loading a demo, or changing model selections', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    await advance();
    await advance();
    expect(screen.getByRole('heading', { name: '评测工作区' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '电脑文件' })).toHaveAttribute('aria-pressed', 'false');
    await advance();
    expect(within(guide()).getByText(/自动生成/)).toBeInTheDocument();
    for (let index = 3; index < 8; index += 1) await advance();
    expect(within(guide()).getByText(/先完成一次评测/)).toBeInTheDocument();
    for (let index = 8; index < 12; index += 1) await advance();
    await userEvent.click(within(guide()).getByRole('button', { name: '完成教程' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '暂无评测结果' })).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
    expect(requests.mock.calls.some(([url]) => url === '/api/models')).toBe(false);
  });

  it('offers welcome once, restores focus on Escape, and can restart at the beginning', async () => {
    localStorage.clear();
    render(<App/>);
    expect(guide()).toBeInTheDocument();
    await advance();
    await userEvent.keyboard('{Escape}');
    const trigger = screen.getByRole('button', { name: '使用教程' });
    expect(trigger).toHaveFocus();
    expect(localStorage.getItem('veriscope:evaluation-guide')).toBe('done');
    await userEvent.click(trigger);
    expect(within(guide()).getByText('1 / 13')).toBeInTheDocument();
    await advance();
    await userEvent.click(within(guide()).getByRole('button', { name: '上一步' }));
    expect(within(guide()).getByText('1 / 13')).toBeInTheDocument();
    await userEvent.click(within(guide()).getByRole('button', { name: '跳过' }));
    expect(trigger).toHaveFocus();
  });

  it('does not interrupt a deep link and closes when the user chooses a different page', async () => {
    localStorage.clear();
    render(<App entryRequest={{ id: 1, target: 'lab', view: 'trace' }}/>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    await userEvent.click(screen.getByRole('button', { name: '评测工作区' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('keeps the highlight on its target after scrolling and puts the card outside that target', async () => {
    let targetTop = 500;
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const isTarget = this.getAttribute('data-evaluation-guide') === 'new';
      const top = isTarget ? targetTop : 0;
      const left = isTarget ? 500 : 0;
      const width = isTarget ? 140 : 400;
      const height = isTarget ? 40 : this.classList.contains('evaluation-guide-tip') ? 250 : 0;
      return { top, left, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) };
    });
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    await advance();
    const highlight = document.querySelector<HTMLElement>('.evaluation-guide-spot')!;
    const card = document.querySelector<HTMLElement>('.evaluation-guide-tip')!;
    expect(highlight).toHaveStyle({ top: '495px', left: '495px' });
    expect(Number.parseFloat(card.style.top) + 250).toBeLessThan(targetTop);
    targetTop = 200;
    fireEvent.scroll(window);
    await waitFor(() => expect(highlight).toHaveStyle({ top: '195px' }));
    expect(Number.parseFloat(card.style.top)).toBeGreaterThan(targetTop + 40);
    await userEvent.keyboard('{Escape}');
    expect(document.querySelector('.evaluation-guide-spot')).toBeNull();
  });

  it('keeps manual walkthrough usable when browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    render(<App/>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    await finishGuide();
    expect(screen.getByRole('button', { name: '使用教程' })).toHaveFocus();
    expect(writes()).toHaveLength(0);
  });

  it('keeps the card inside the viewport when an oversized target leaves no free side', async () => {
    vi.stubGlobal('innerWidth', 1280);
    vi.stubGlobal('innerHeight', 720);
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const isTarget = this.getAttribute('data-evaluation-guide') === 'models';
      const top = isTarget ? -440 : 0;
      const left = isTarget ? 300 : 0;
      const width = isTarget ? 950 : 400;
      const height = isTarget ? 1600 : this.classList.contains('evaluation-guide-tip') ? 250 : 0;
      return { top, left, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) };
    });
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    for (let index = 0; index < 4; index += 1) await advance();
    const card = document.querySelector<HTMLElement>('.evaluation-guide-tip')!;
    const top = Number.parseFloat(card.style.top);
    const maxHeight = Number.parseFloat(card.style.maxHeight);
    expect(top).toBeGreaterThanOrEqual(16);
    expect(top + Math.min(250, maxHeight)).toBeLessThanOrEqual(704);
    expect(maxHeight).toBeLessThanOrEqual(688);
    expect(within(guide()).getByRole('button', { name: '下一步' })).toBeEnabled();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('explains that a demo snapshot cannot be saved instead of instructing a disabled download', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    for (let index = 0; index < 11; index += 1) await advance();
    expect(within(guide()).getByText(/演示快照.*不能保存/)).toBeInTheDocument();
    expect(within(guide()).queryByText(/点「保存记录」将 JSON 下载/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存记录' })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });

  it.each(['readOnly', 'staticSite'] as const)('explains the %s boundary without creating results', async (key) => {
    mode[key] = true;
    if (key === 'staticSite') mode.readOnly = true;
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    expect(within(guide()).getByText(/只读|无后端/)).toBeInTheDocument();
    await finishGuide();
    expect(screen.getByRole('button', { name: '保存记录' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '历史记录' })).toBeDisabled();
    expect(screen.queryByText('演示快照')).not.toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });

  it('preserves configured judge, thresholds, and a real result across all guide navigation', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
    fireEvent.change(screen.getByLabelText('评测裁判'), { target: { value: 'qwen-flash' } });
    await userEvent.click(screen.getByLabelText('沿用后端默认阈值'));
    fireEvent.change(screen.getByRole('spinbutton', { name: '忠实度门禁阈值' }), { target: { value: '0.65' } });
    await userEvent.click(screen.getByRole('button', { name: '选择 GLM-5.2-Fast' }));
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    expect(await screen.findByText('真实评测结果')).toBeInTheDocument();
    const before = writes().length;
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    await finishGuide();
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
    expect(writes()).toHaveLength(before);
    await userEvent.click(screen.getByRole('button', { name: '评测工作区' }));
    expect(screen.getByLabelText('评测裁判')).toHaveValue('qwen-flash');
    expect(screen.getByRole('spinbutton', { name: '忠实度门禁阈值' })).toHaveValue(0.65);
    expect(screen.getByRole('button', { name: '选择 GLM-5.2-Fast' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('does not repeat an active evaluation when navigating back and forward in the guide', async () => {
    let complete!: (result: Response) => void;
    requests.mockImplementation((url: string) => url === '/api/evaluate'
      ? new Promise<Response>((resolve) => { complete = resolve; })
      : Promise.resolve(response(url === '/api/health' ? { status: 'ok' } : { models: [] })));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await userEvent.click(screen.getByRole('button', { name: '使用教程' }));
    await advance();
    await advance();
    await userEvent.click(within(guide()).getByRole('button', { name: '上一步' }));
    await advance();
    expect(writes()).toHaveLength(1);
    expect(screen.getByRole('progressbar', { name: '评测进度' })).toBeInTheDocument();
    await act(async () => { complete(response(demo.result)); });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
  });
});
