import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FixVerificationView } from '../features/FixVerificationView';
import { FormalRunReplay } from '../features/FormalRunReplay';
import { parseLabHash } from '../labRoute';

const region = (name: string) => screen.getByRole('region', { name });

describe('Fix verification view', () => {
  it('tells the story from blocked to passed, and says the rerun was not a one-line diff', () => {
    render(<FixVerificationView/>);
    const story = region('从拦下到放行');
    expect(within(story).getByText('10-05 · 正向忠实度 0.7375 < 0.8，FAIL')).toBeInTheDocument();
    expect(within(story).getByText('F01、F03、F04、F05 这 4 道错误前提题都回答「无法回答」，按 0 计')).toBeInTheDocument();
    expect(within(story).getByText('10-14 · 正向忠实度 0.8607 ≥ 0.8，PASS')).toBeInTheDocument();
    expect(within(story).getByText('25 题检索资料逐题相同（25 / 25）')).toBeInTheDocument();
    expect(within(story).getByText(/另有 15 个源文件不同.*不是严格的「只差一句」/)).toBeInTheDocument();
  });

  it('shows both gate conclusions as uncertain, not a confident pass', () => {
    render(<FixVerificationView/>);
    const gate = region('门禁两次的结论');
    expect(within(gate).getByText('区间 0.55–0.9 · 证据不足')).toBeInTheDocument();
    expect(within(gate).getByText('区间 0.7286–0.9667 · 证据不足')).toBeInTheDocument();
    expect(within(gate).getByText(/FAIL 和 PASS 都是「证据不足」/)).toBeInTheDocument();
  });

  it('lists changed and unfixed questions first, and can show all 25', () => {
    render(<FixVerificationView/>);
    const questions = region('逐题对照');
    const ids = () => [...questions.querySelectorAll('.fx-id')].map((node) => node.textContent);
    expect(ids()).toEqual(['D01', 'D08', 'D09', 'F01', 'F03', 'F04', 'F05']);
    expect(within(questions).getAllByText('拒答 → 纠正前提')).toHaveLength(3);
    expect(within(questions).getByText('仍拒答')).toBeInTheDocument();
    fireEvent.click(within(questions).getByRole('button', { name: '全部 25 题' }));
    expect(ids()).toHaveLength(25);
    expect(within(questions).getAllByText('仍正确拒答')).toHaveLength(5);
    expect(within(questions).getByText(/逐题变化的 95% 区间 -0.025–0.2923，包含 0/)).toBeInTheDocument();
  });

  it('says the PASS depends on the judge', () => {
    render(<FixVerificationView/>);
    const limits = region('这份对照不能说明什么');
    expect(within(limits).getByText(/换 Qwen\/Qwen2.5-72B-Instruct 作裁判重跑，正向忠实度 0.7992，门禁 FAIL/)).toBeInTheDocument();
    expect(within(limits).getByRole('link', { name: '独立裁判复核' })).toBeInTheDocument();
  });

  it('reports the held-out questions with their weak p-value and the one exception', () => {
    render(<FixVerificationView/>);
    const heldout = region('换一批新题再验证');
    expect(within(heldout).getByText('9 / 14 → 14 / 14')).toBeInTheDocument();
    expect(within(heldout).getByText('p = 0.0625')).toBeInTheDocument();
    expect(within(heldout).getByText(/唯一没做到「全部陈述有依据」的是 AF06/)).toBeInTheDocument();
  });

  it('is reachable by deep link and from the formal-run replay', () => {
    expect(parseLabHash('#/lab/demo/fix')).toEqual({ target: 'demo', view: 'fix' });
    render(<FormalRunReplay/>);
    expect(screen.getByRole('link', { name: '查看修复验证' })).toHaveAttribute('href', '#/lab/demo/fix');
  });
});
