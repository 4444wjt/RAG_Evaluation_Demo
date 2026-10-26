import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { BlindspotView, exportBlindspotReview, reviewCanExport } from '../features/BlindspotView';
import type { BlindspotReview } from '../features/BlindspotView';
import data from '../fixtures/blindspot-check.json';
import { parseLabHash } from '../labRoute';

vi.mock('../components/Charts', () => ({ AbilityChart: () => null, RiskChart: () => null, DurationChart: () => null }));
const step = (name: string) => within(screen.getByRole('navigation', { name: '体检闭环步骤' })).getByRole('button', { name: new RegExp(name) });
const empty = (): BlindspotReview => ({ diagnosis: null, treatmentViewed: false, sourceChecked: false, resultsChecked: false, decision: null, note: '' });
const complete = (): BlindspotReview => ({ diagnosis: 'paragraph_labels', treatmentViewed: true, sourceChecked: true, resultsChecked: true, decision: 'confirmed', note: '仅确认历史对照' });
beforeEach(() => vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"ok"}', { headers: { 'Content-Type': 'application/json' } }))));

describe('Blindspot historical workflow', () => {
  it('keeps original results, sample dependence, and the no-model boundary visible', () => {
    render(<BlindspotView/>);
    const results = screen.getByRole('table', { name: '历史体检与复测结果' });
    expect(within(results).getByText('1 / 10')).toBeInTheDocument();
    expect(within(results).getByText('10 / 10')).toBeInTheDocument();
    expect(within(results).getAllByText('0 / 5')).toHaveLength(2);
    expect(screen.getByText('历史回放 · 不调用模型')).toBeInTheDocument();
    expect(screen.getByText(/衍生变体并非独立样本/)).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('lets readers inspect a different archived statement with its actual judge reason', () => {
    render(<BlindspotView/>);
    const selected = data.rows.find((row) => row.id === 'paragraph_existing-A42P1toP2')!;
    fireEvent.change(screen.getByLabelText('选择款级题'), { target: { value: selected.id } });
    expect(screen.getByText(selected.statement)).toBeInTheDocument();
    expect(screen.getByText(selected.before.reason)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '原节选下的裁判判断：矛盾' })).toBeInTheDocument();
  });

  it('renders exact archived texts and highlights only recorded insertions', () => {
    render(<BlindspotView/>);
    fireEvent.click(step('处理'));
    expect(screen.getByLabelText('处理前原节选').textContent).toBe(data.contexts.plain);
    const labeled = screen.getByLabelText('补标签后节选');
    expect(labeled.textContent).toBe(data.contexts.labeled);
    expect([...labeled.querySelectorAll('mark')].map((node) => node.textContent)).toEqual(data.contexts.insertions.map((insertion) => insertion.text));
  });

  it('does not equate navigation or a selected opinion with completed review', () => {
    render(<BlindspotView/>);
    fireEvent.click(step('复测与复核'));
    expect(screen.getByRole('button', { name: '导出复核记录' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('复核意见'), { target: { value: 'confirmed' } });
    fireEvent.click(screen.getByLabelText('已核对原文和标签改动'));
    fireEvent.click(screen.getByLabelText('已核对逐题结果和正确对照'));
    expect(screen.getByRole('button', { name: '导出复核记录' })).toBeDisabled();
  });

  it('completes a traceable review only after an explicit diagnosis, treatment and results check', () => {
    render(<BlindspotView/>);
    fireEvent.click(step('诊断'));
    fireEvent.click(screen.getByLabelText('采用补款号标签的处理方向'));
    fireEvent.click(screen.getByRole('button', { name: '查看标签处理' }));
    fireEvent.click(screen.getByRole('button', { name: '已查看处理，核对归档复测' }));
    expect(within(screen.getByRole('table', { name: '款级逐题复测' })).getAllByRole('row')).toHaveLength(11);
    fireEvent.click(screen.getByText('查看 5 条正确对照'));
    const controlList = screen.getByText('查看 5 条正确对照').parentElement!;
    expect(within(controlList).getAllByRole('listitem')).toHaveLength(5);
    fireEvent.click(screen.getByLabelText('已核对原文和标签改动'));
    fireEvent.click(screen.getByLabelText('已核对逐题结果和正确对照'));
    fireEvent.change(screen.getByLabelText('复核意见'), { target: { value: 'confirmed' } });
    expect(screen.getByRole('button', { name: '导出复核记录' })).toBeEnabled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('can export an unresolved review without filling missing checks or asserting completion', () => {
    const state = { ...empty(), decision: 'needs_review' as const, note: ' 需要进一步检查因果解释 ' };
    const result = exportBlindspotReview(state, '2026-10-23T12:00:00Z');
    expect(result.completed).toBe(false);
    expect(result.review.sourceChecked).toBe(false);
    expect(result.review.diagnosis).toBeNull();
    expect(result.review.note).toBe('需要进一步检查因果解释');
    expect(result.reviewer).toEqual({ identity: null, independentHumanVerified: false });
    expect(result.modelCalls).toBe(0);
    expect(result.archivedResults).toEqual(data.stats);
  });

  it('requires a reason for unresolved exports and rejects incomplete confirmed exports', () => {
    expect(() => exportBlindspotReview(empty(), 'now')).toThrow('请完成复核');
    expect(() => exportBlindspotReview({ ...empty(), decision: 'needs_review', note: ' ' }, 'now')).toThrow();
    expect(reviewCanExport({ ...complete(), treatmentViewed: false })).toBe(false);
    expect(reviewCanExport({ ...complete(), diagnosis: 'uncertain' })).toBe(false);
    expect(reviewCanExport({ ...complete(), sourceChecked: false })).toBe(false);
    expect(reviewCanExport({ ...complete(), resultsChecked: false })).toBe(false);
  });

  it('binds exported review to the actual source, context hashes and historical results', () => {
    const result = exportBlindspotReview(complete(), '2026-10-23T12:00:00Z');
    expect(result.schema).toBe('blindspot-review.v1');
    expect(result.source.registrationSha256).toBe(data.source.registrationSha256);
    expect(result.source.packageSha256).toBe(data.source.packageSha256);
    expect(result.source.recordsSha256).toBe(data.source.recordsSha256);
    expect(result.contextHashes).toEqual({ plain: data.contexts.plainSha256, labeled: data.contexts.labeledSha256 });
    expect(result.completed).toBe(true);
    expect(result.mode).toBe('historical_replay');
    expect(result.modelCalls).toBe(0);
  });

  it('supports direct routes and appears in the product without attaching current-run metrics', async () => {
    expect(parseLabHash('#/lab/blindspot')).toEqual({ target: 'lab', view: 'blindspot' });
    expect(parseLabHash('#/lab/demo/blindspot')).toEqual({ target: 'demo', view: 'blindspot' });
    render(<App/>);
    fireEvent.click(screen.getByRole('button', { name: '加载演示' }));
    fireEvent.click(screen.getByRole('button', { name: '盲区体检' }));
    expect(screen.getByRole('heading', { level: 1, name: '盲区体检' })).toBeInTheDocument();
    expect(screen.queryByText('查看演示数据来源')).not.toBeInTheDocument();
    await screen.findByText('服务可连接');
    expect(vi.mocked(fetch).mock.calls.every(([url]) => url === '/api/health')).toBe(true);
  });
});
