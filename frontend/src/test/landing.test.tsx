import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import { EvidenceFlow } from '../features/landing/EvidenceFlow';
import { EvidenceJourney } from '../features/landing/EvidenceJourney';
import { LandingPage } from '../features/landing/LandingPage';
import demoFixture from '../fixtures/demo.json';

describe('Landing page', () => {
  beforeEach(() => { vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null); });
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
  it('explains the five stages and labels the demonstration', () => {
    render(<LandingPage onEnter={vi.fn()}/>);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('让 RAG 的回答，经得起证据检查。');
    for (const name of ['提出问题', '检索上下文', '模型回答', '核对证据', '质量门禁']) expect(within(screen.getByRole('list', {name:'证据验证流程'})).getByText(name)).toBeInTheDocument();
    for (const name of ['Question', 'Retrieval Context', 'Answer', 'Evidence Check', 'Quality Gate']) expect(screen.queryByText(name)).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText(/门禁按样本与阈值判定/)).toBeInTheDocument();
    expect(screen.getByText(/无需连接模型服务/)).toBeInTheDocument();
  });

  it('passes explicit entry intents and retains link destinations', () => {
    const onEnter = vi.fn();
    render(<LandingPage onEnter={onEnter}/>);
    const demo = screen.getAllByRole('link', { name: '查看演示' })[0];
    expect(demo).toHaveAttribute('href', '#/lab/demo');
    fireEvent.click(demo);
    expect(onEnter).toHaveBeenLastCalledWith('demo');
    fireEvent.click(screen.getAllByRole('link', { name: '配置评测' })[0]);
    expect(onEnter).toHaveBeenLastCalledWith('workspace');
    fireEvent.click(screen.getByRole('link', { name: '进入工作台' }));
    expect(onEnter).toHaveBeenLastCalledWith('lab');
    onEnter.mockClear();
    fireEvent.click(demo, { ctrlKey: true });
    expect(onEnter).not.toHaveBeenCalled();
  });

  it('presents crisp native product text without bitmap previews or nested controls', () => {
    const { container } = render(<LandingPage onEnter={vi.fn()}/>);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByRole('list', { name: '证据验证流程' }).children).toHaveLength(5);
    const gate = screen.getByRole('region', { name: 'DeepSeek 演示快照质量门禁' });
    const evidence = screen.getByRole('region', { name: 'DeepSeek 演示快照逐句判定' });
    expect(gate).toHaveTextContent('PASS');
    expect(within(evidence).getByText('星云科技最新一轮B轮融资金额达5亿元人民币。')).toBeInTheDocument();
    expect(within(evidence).getByText('星云科技已累计获得三轮融资，最新一轮B轮融资金额达5亿元人民币。')).toBeInTheDocument();
    expect(evidence).toHaveTextContent('有依据');
    expect(evidence).toHaveTextContent('精确匹配检索上下文原文');
    for (const preview of container.querySelectorAll('.landing-product-preview')) {
      expect(preview.querySelector('img, canvas, button, a, input, select, textarea, summary, details')).toBeNull();
    }
  });

  it('retains authentic preview provenance and the evaluation boundary', () => {
    render(<LandingPage onEnter={vi.fn()}/>);
    expect(screen.getByText('真实界面 · 演示快照 20260930-1958 · 非本次实时评测')).toBeInTheDocument();
    expect(screen.getByText('真实界面 · 同一演示快照中的逐句证据')).toBeInTheDocument();
    expect(screen.getByText(/不等同于单条回答判断，也不代表所有指标优秀/)).toBeInTheDocument();
    const gate = screen.getByRole('region', { name: 'DeepSeek 演示快照质量门禁' });
    expect(gate).toHaveTextContent('演示门禁范围');
    expect(gate).toHaveTextContent('当前快照包含 6 个问题、3 个模型、18 条回答。');
    expect(gate).toHaveTextContent('本快照用于展示评测流程；PASS 仅表示指定正向样本达到现有门槛，不代表模型总体可靠性。');
  });

  it('keeps the native preview equal to the original frozen snapshot', () => {
    render(<LandingPage onEnter={vi.fn()}/>);
    const rows = demoFixture.result.model_dfs['deepseek-chat'];
    const summary = demoFixture.result.summary.find(row => row.model === 'deepseek-chat')!;
    const thresholds = demoFixture.provenance.run.thresholds;
    const gate = within(screen.getByRole('region', { name: 'DeepSeek 演示快照质量门禁' }));
    expect(gate.getByText('PASS', { exact: true })).toBeInTheDocument();
    expect(gate.getByText('DeepSeek', { exact: true })).toBeInTheDocument();
    expect(gate.getByText(`${rows.filter(row => row.case_type === 'positive').length} 条正向用例`, { exact: true })).toBeInTheDocument();
    for (const [field, metric, threshold] of [
      ['pos_faithfulness', summary.pos_faithfulness, thresholds.faithfulness],
      ['pos_context_precision', summary.pos_context_precision, thresholds.context_precision],
    ] as const) {
      const node = screen.getByRole('region', { name: 'DeepSeek 演示快照质量门禁' }).querySelector(`[data-gate-field="${field}"]`)!;
      expect(node).toHaveTextContent(`${(metric * 100).toFixed(1)}%`);
      expect(node).toHaveTextContent(`门槛 ≥ ${(threshold * 100).toFixed(1)}%`);
    }
    expect(gate.getByText('两项正向指标均达到门槛', { exact: true })).toBeInTheDocument();
    const original = JSON.parse(rows[0].hallucination_trace).statements[0] as {text:string;verdict:string;evidence:string};
    expect(original.verdict).toBe('supported');
    expect(rows[0].contexts).toContain(original.evidence);
    const evidence = within(screen.getByRole('region', { name: 'DeepSeek 演示快照逐句判定' }));
    expect(evidence.getByText(original.text, { exact: true })).toBeInTheDocument();
    expect(evidence.getByText(original.evidence, { exact: true })).toBeInTheDocument();
    expect(evidence.getByText('有依据', { exact: true })).toBeInTheDocument();
  });

  it('presents the existing platform capabilities in their workflow order', () => {
    const { container } = render(<LandingPage onEnter={vi.fn()}/>);
    const titles = [...container.querySelectorAll('.landing-capabilities dt')].map(node => node.textContent?.replace(/^\d{2}/, ''));
    expect(titles).toEqual(['评测工作区', '多模型对比', '幻觉分析', '评测报告']);
    expect(container.querySelectorAll('.landing-capabilities dd')).toHaveLength(4);
    expect(screen.getByText('导入并查看已有 HTML 评测报告。')).toBeInTheDocument();
  });

  it('explains the evidence inspection relationship without creating a new verdict', () => {
    const { container } = render(<EvidenceJourney enabled/>);
    expect(screen.getByRole('list', {name:'证据回查路径'})).toHaveTextContent('实验结论逐句判定检索上下文');
    expect(screen.getByText('回查链路示意')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('PASS')).not.toBeInTheDocument();
    fireEvent.animationEnd(container.querySelector('.journey-pulse-1')!);
    expect(container.querySelector('.evidence-journey')).toHaveAttribute('data-complete', 'true');
  });

  it('ends the inspection illustration when reduced motion is enabled and does not replay', () => {
    const { container, rerender } = render(<EvidenceJourney enabled/>);
    rerender(<EvidenceJourney enabled={false}/>);
    expect(container.querySelector('.evidence-journey')).toHaveAttribute('data-complete', 'true');
    rerender(<EvidenceJourney enabled/>);
    expect(container.querySelector('.evidence-journey')).toHaveAttribute('data-playing', 'false');
    expect(screen.getByText('逐句判定')).toBeVisible();
  });

  it('disconnects the inspection visibility observer on unmount', () => {
    const disconnect = vi.fn();
    vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect = disconnect; });
    const { unmount } = render(<EvidenceJourney enabled/>);
    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it('plays exactly two passes and keeps the final gate visible under StrictMode', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    const { container, unmount } = render(<StrictMode><EvidenceFlow/></StrictMode>);
    const flow = container.querySelector('.evidence-flow')!;
    expect(flow).toHaveAttribute('data-cycle', '1');
    act(() => { vi.advanceTimersByTime(1800); });
    expect(screen.getByText('质量门禁').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(flow).toHaveAttribute('data-complete', 'false');
    act(() => { vi.advanceTimersByTime(600); });
    expect(flow).toHaveAttribute('data-cycle', '2');
    expect(screen.getByText('提出问题').closest('li')).toHaveAttribute('aria-current', 'step');
    act(() => { vi.advanceTimersByTime(2400); });
    expect(flow).toHaveAttribute('data-complete', 'true');
    expect(flow).toHaveAttribute('data-playing', 'false');
    expect(screen.getByText('质量门禁').closest('li')).toHaveAttribute('aria-current', 'step');
    act(() => { vi.advanceTimersByTime(10000); });
    expect(flow).toHaveAttribute('data-cycle', '2');
    expect(vi.getTimerCount()).toBe(0);
    unmount();
  });

  it('pauses while hidden and resumes the remaining time without restarting', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    const { container, unmount } = render(<EvidenceFlow/>);
    const flow = container.querySelector('.evidence-flow')!;
    act(() => { vi.advanceTimersByTime(700); });
    hidden.mockReturnValue(true);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); vi.advanceTimersByTime(10000); });
    expect(flow).toHaveAttribute('data-paused', 'true');
    expect(screen.getByText('检索上下文').closest('li')).toHaveAttribute('aria-current', 'step');
    hidden.mockReturnValue(false);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); vi.advanceTimersByTime(4100); });
    expect(flow).toHaveAttribute('data-complete', 'true');
    expect(flow).toHaveAttribute('data-cycle', '2');
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('finishes immediately when motion is reduced and never restarts on preference recovery', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    const { container, rerender } = render(<EvidenceFlow enabled/>);
    act(() => { vi.advanceTimersByTime(800); });
    rerender(<EvidenceFlow enabled={false}/>);
    expect(container.querySelector('.evidence-flow')).toHaveAttribute('data-complete', 'true');
    expect(screen.getByText('质量门禁').closest('li')).toHaveAttribute('aria-current', 'step');
    rerender(<EvidenceFlow enabled/>);
    act(() => { vi.advanceTimersByTime(10000); });
    expect(container.querySelector('.evidence-flow')).toHaveAttribute('data-playing', 'false');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('honors reduced motion on initial load while retaining all Chinese content', () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    render(<LandingPage onEnter={vi.fn()}/>);
    expect(within(screen.getByRole('list', {name:'证据验证流程'})).getByText('质量门禁').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText(/流程示意：逐句证据与门禁指标分别呈现/)).toBeInTheDocument();
  });

  it('clears pending playback timers when the landing page unmounts', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
    const { unmount } = render(<EvidenceFlow/>);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
