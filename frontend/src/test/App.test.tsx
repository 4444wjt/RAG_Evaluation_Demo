import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import type { DemoFixture, EvaluationResult } from '../api/types';
import demo from '../fixtures/demo.json';
import { OverviewView } from '../features/OverviewView';
import { initialConfig } from '../state';

vi.mock('../components/Charts', () => ({
  AbilityChart: () => <div aria-label="能力图测试替身"/>,
  RiskChart: () => <div aria-label="风险图测试替身"/>,
  DurationChart: () => <div aria-label="耗时图测试替身"/>,
}));

const sample = (): EvaluationResult => structuredClone(demo.result) as EvaluationResult;
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let requests: ReturnType<typeof vi.fn>;

beforeEach(() => {
  requests = vi.fn().mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : response(sample())));
  vi.stubGlobal('fetch', requests);
});

async function openWorkspace() {
  await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
}

const summaryModels = [
  { id: 'deepseek-chat', label: 'DeepSeek-V4.1-Flash', values: ['0', '0', '0', '100.0%'], gate: 'PASS' },
  { id: 'Pro/Qwen/Qwen2.5-7B-Instruct', label: 'Qwen2.5-7B', values: ['3', '0', '3', '100.0%'], gate: 'FAIL' },
  { id: 'glm-5.2-fast-preview', label: 'GLM-5.2-Fast', values: ['0', '0', '0', '100.0%'], gate: 'PASS' },
];
const summaryLabels = ['幻觉语句数', '事实错误数', '无依据编造数', '拒答准确率'];

function expectSummaryValues(values: string[]) {
  summaryLabels.forEach((label, index) => {
    const tile = screen.getByText(label).closest('.metric-tile')!;
    expect(tile.querySelector('strong')?.textContent).toBe(values[index]);
  });
}

function expectGateContext(model: string, range: string, faith: string, contextPrecision: string, faithThreshold = '门槛 ≥ 80.0%', contextThreshold = '门槛 ≥ 80.0%') {
  const context = screen.getByLabelText('门禁检查上下文');
  expect(context.querySelector('.gate-subject dd')).toHaveTextContent(model);
  expect(context.querySelector('.gate-range dd')).toHaveTextContent(range);
  expect(context.querySelector('[data-gate-field="pos_faithfulness"] dd > span')).toHaveTextContent(faith);
  expect(context.querySelector('[data-gate-field="pos_context_precision"] dd > span')).toHaveTextContent(contextPrecision);
  expect(context.querySelector('[data-gate-field="pos_faithfulness"] dd > small')).toHaveTextContent(faithThreshold);
  expect(context.querySelector('[data-gate-field="pos_context_precision"] dd > small')).toHaveTextContent(contextThreshold);
}

describe('Workspace UI integration with mocked API', () => {
  it('initial state and health check do not run an evaluation', async () => {
    render(<App/>);
    expect(screen.getByText('暂无评测结果')).toBeInTheDocument();
    await screen.findByText('服务可连接');
    expect(requests.mock.calls.every(([url]) => url === '/api/health')).toBe(true);
    expect(screen.queryByText('PASS')).not.toBeInTheDocument();
  });

  it('loads a sourced offline demo without posting and shares it across views', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    expect(screen.getByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('演示快照')).toBeInTheDocument();
    expect(screen.getByText('演示门禁范围')).toBeInTheDocument();
    expect(screen.getByText('当前快照包含 6 个问题、3 个模型、18 条回答。')).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: '多模型对比' }));
    expect(screen.getByRole('table', { name: /各模型的全部原始指标/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '幻觉分析' }));
    expect(screen.getByRole('heading', { name: '逐句溯源' })).toBeInTheDocument();
  });

  it('binds the frozen demo source but clears it after a mocked live run', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    await userEvent.click(screen.getByRole('button', { name: '幻觉分析' }));
    expect(screen.getByText(/虚构示例资料，不是权威来源/)).toBeInTheDocument();
    expect(screen.getByText('资料来源：缺少版本')).toBeInTheDocument();
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('真实评测结果');
    await userEvent.click(screen.getByRole('button', { name: '幻觉分析' }));
    expect(screen.getByText('本次评测结果未携带资料哈希，无法与登记表对应')).toBeInTheDocument();
    expect(screen.queryByText(/虚构示例资料，不是权威来源/)).not.toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });

  it('runs once, preserves 0 thresholds and completes with a current PASS and the original gate=false record', async () => {
    const result = sample();
    result.gate_passed = false;
    result.summary[0].hallucination_statement_count = 17;
    result.summary[0].trace_fact_error_count = 2;
    result.summary[0].trace_fabrication_count = 3;
    result.summary[0].refusal_accuracy = 0;
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : response(result)));
    render(<App/>);
    await openWorkspace();
    await userEvent.click(screen.getByRole('checkbox', { name: '沿用后端默认阈值' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: '忠实度门禁阈值' }), { target: { value: '0' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: '上下文精度门禁阈值' }), { target: { value: '0' } });
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('PASS');
    expect(screen.getByText('原始评测门禁：FAIL')).toBeInTheDocument();
    expect(screen.queryByText('本次评测未完成')).not.toBeInTheDocument();
    expect(screen.getByText('17')).toBeInTheDocument();
    expect(screen.getByText('0.0%')).toBeInTheDocument();
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%', '门槛 ≥ 0.0%', '门槛 ≥ 0.0%');
    expect(screen.getByText('阈值来自本次显式提交参数。')).toBeInTheDocument();
    const calls = requests.mock.calls.filter(([url]) => url === '/api/evaluate');
    expect(calls).toHaveLength(1);
    expect(JSON.parse(calls[0][1].body)).toMatchObject({ faith_threshold: 0, ctx_prec_threshold: 0, version_label: null });
  });

  it('shows missing summary KPIs as unavailable, without making up count relationships', async () => {
    const result = sample();
    result.summary[0].hallucination_statement_count = null;
    result.summary[0].trace_fact_error_count = 2;
    result.summary[0].trace_fabrication_count = 3;
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : response(result)));
    render(<App/>);
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('WARNING');
    expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
    const tile = screen.getByText('幻觉语句数').closest('.metric-tile')!;
    expect(within(tile as HTMLElement).getByText('暂无数据')).toBeInTheDocument();
  });

  it('blocks an empty selection without calling evaluate', async () => {
    render(<App/>);
    await openWorkspace();
    for (const label of ['选择 DeepSeek-V4.1-Flash', '选择 Qwen2.5-7B', '选择 GLM-5.2-Fast']) await userEvent.click(screen.getByRole('button', { name: label }));
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    expect(screen.getByText('请至少选择一个模型。')).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
  });

  it('prevents duplicate submissions and retains a clearly marked previous result', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url === '/api/health' ? Promise.resolve(response({ status: 'ok' })) : new Promise<Response>((resolve) => { finish = resolve; }));
    render(<StrictMode><App/></StrictMode>);
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    await openWorkspace();
    const button = screen.getByRole('button', { name: '开始评测' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(screen.getByRole('button', { name: '加载演示' })).toBeDisabled();
    expect(screen.getByText('上次结果 · 本次尚未更新')).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
    await act(async () => { finish(response(sample())); });
    await screen.findByText('真实评测结果');
    expect(screen.queryByText('上次结果 · 本次尚未更新')).not.toBeInTheDocument();
  });

  it.each([403, 404, 409, 422, 500])('shows a safe actionable %s error without vendor text', async (status) => {
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : response({ detail: 'provider-secret traceback' }, status)));
    render(<App/>);
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('本次评测未完成');
    expect(screen.getByRole('alert')).not.toHaveTextContent('provider-secret');
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });

  it('treats a transport failure as unknown, never as cancellation or automatic retry', async () => {
    requests.mockImplementation((url: string) => url === '/api/health' ? Promise.resolve(response({ status: 'ok' })) : Promise.reject(new TypeError('connection lost')));
    render(<App/>);
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('评测结果未知');
    expect(screen.getByRole('alert')).toHaveTextContent('服务端可能仍在运行');
    await waitFor(() => expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1));
  });
});

describe('Dashboard summary model selection', () => {
  it('switches all three demo models and their KPIs and current gate without changing the original gate or making requests', async () => {
    render(<App/>);
    await screen.findByText('服务可连接');
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    const selector = screen.getByRole('combobox', { name: '当前摘要模型' });
    expect(selector).toHaveValue(summaryModels[0].id);
    const before = requests.mock.calls.length;
    for (const model of summaryModels) {
      expect(within(selector).getByRole('option', { name: model.label })).toBeInTheDocument();
      await userEvent.selectOptions(selector, model.id);
      expect(selector).toHaveValue(model.id);
      expect(within(selector).getByRole('option', { name: model.label, selected: true })).toBeInTheDocument();
      expectSummaryValues(model.values);
      expect(screen.getByText(model.gate)).toBeInTheDocument();
      expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
    }
    expect(requests).toHaveBeenCalledTimes(before);
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
  });

  it('uses each live row with distinct KPIs and warns when runtime thresholds are unavailable while retaining the original FAIL', async () => {
    const result = sample();
    result.gate_passed = false;
    result.summary.forEach((row, index) => {
      row.hallucination_statement_count = index + 4;
      row.trace_fact_error_count = index + 7;
      row.trace_fabrication_count = index + 10;
      row.refusal_accuracy = (index + 1) / 4;
    });
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : response(result)));
    render(<App/>);
    await screen.findByText('服务可连接');
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('WARNING');
    expect(screen.getByText('原始评测门禁：FAIL')).toBeInTheDocument();
    const selector = screen.getByRole('combobox', { name: '当前摘要模型' });
    const before = requests.mock.calls.length;
    for (const [index, model] of summaryModels.entries()) {
      await userEvent.selectOptions(selector, model.id);
      expect(within(selector).getByRole('option', { name: model.label, selected: true })).toBeInTheDocument();
      expectSummaryValues([String(index + 4), String(index + 7), String(index + 10), `${(index + 1) * 25}.0%`]);
      expect(screen.getByText('WARNING')).toBeInTheDocument();
      expect(screen.getByText('原始评测门禁：FAIL')).toBeInTheDocument();
    }
    expect(requests).toHaveBeenCalledTimes(before);
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });

  const callbacks = { onConfigure: vi.fn(), onDemo: vi.fn(), onCompare: vi.fn(), onTrace: vi.fn() };

  it('skips rows without a model ID when choosing the first valid default and building options', () => {
    const result = sample();
    const missingModel = result.summary[0];
    missingModel.faithfulness = 0.8;
    delete missingModel.model;
    const emptyModel = result.summary[2];
    emptyModel.model = '';
    emptyModel.faithfulness = 0.9;
    result.summary = [missingModel, emptyModel, result.summary[1]];
    result.gate_passed = false;
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    const selector = screen.getByRole('combobox', { name: '当前摘要模型' });
    expect(selector).toHaveValue(summaryModels[1].id);
    expect(within(selector).getAllByRole('option')).toHaveLength(1);
    expect(within(selector).getByRole('option', { name: summaryModels[1].label, selected: true })).toHaveValue(summaryModels[1].id);
    expectSummaryValues(summaryModels[1].values);
    expect(screen.getByText('FAIL')).toBeInTheDocument();
  });

  it('defaults to the first valid row but keeps an unavailable model selectable, preserving real zero values', async () => {
    const result = sample();
    const row = result.summary[0];
    row.faithfulness = null;
    row.hallucination_statement_count = null;
    delete row.trace_fact_error_count;
    row.trace_fabrication_count = Number.NaN;
    row.refusal_accuracy = 0;
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    const selector = screen.getByRole('combobox', { name: '当前摘要模型' });
    expect(selector).toHaveValue(summaryModels[1].id);
    expectSummaryValues(summaryModels[1].values);
    await userEvent.selectOptions(selector, summaryModels[0].id);
    expect(within(selector).getByRole('option', { name: 'DeepSeek-V4.1-Flash', selected: true })).toBeInTheDocument();
    expectSummaryValues(['暂无数据', '暂无数据', '暂无数据', '0.0%']);
    expect(screen.getByText('PASS')).toBeInTheDocument();
  });

  it('does not substitute zero or another model when all summary rows lack valid faithfulness', async () => {
    const result = sample();
    result.summary.forEach((row) => { row.faithfulness = null; });
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    expectSummaryValues(['暂无数据', '暂无数据', '暂无数据', '暂无数据']);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '当前摘要模型' }), summaryModels[1].id);
    expectSummaryValues(summaryModels[1].values);
  });

  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY])('shows an unavailable refusal rate for %s without hiding real count zeros', async (missing) => {
    const result = sample();
    const row = result.summary[2];
    if (missing === undefined) delete row.refusal_accuracy;
    else row.refusal_accuracy = missing;
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '当前摘要模型' }), summaryModels[2].id);
    expectSummaryValues(['0', '0', '0', '暂无数据']);
  });

  it('keeps selection for the same result object and resets to the first valid model for a new result with the same IDs', async () => {
    const result = sample();
    const { rerender } = render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    const selector = screen.getByRole('combobox', { name: '当前摘要模型' });
    await userEvent.selectOptions(selector, summaryModels[2].id);
    rerender(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks} resultMeta={<span>摘要来源更新</span>}/>);
    expect(selector).toHaveValue(summaryModels[2].id);
    const next = sample();
    next.summary[0].faithfulness = null;
    next.summary[1].hallucination_statement_count = 12;
    rerender(<OverviewView result={next} gateProvenance={demo.provenance} {...callbacks}/>);
    expect(screen.getByRole('combobox', { name: '当前摘要模型' })).toHaveValue(summaryModels[1].id);
    expectSummaryValues(['12', '0', '3', '100.0%']);
  });
});

describe('Current model quality gate transparency', () => {
  const interpretation = '门禁结论仅反映规则检查，不代表所有模型指标优秀。';
  const selectionNote = '当前模型用于指标查看，不代表最佳模型；门禁展示按该模型的正向指标及已有阈值检查，原始记录保持不变。';
  const chartNote = '图中整体均值用于模型横向比较；当前模型门禁仅检查正向忠实度与正向上下文精度。';
  const rule = '原始门禁采用首个有效评测模型的正向指标；当前卡片检查所选模型，不改写原始记录。';
  const callbacks = { onConfigure: vi.fn(), onDemo: vi.fn(), onCompare: vi.fn(), onTrace: vi.fn() };

  it('separates the conclusion from four KPIs and keeps original records in a collapsed disclosure', async () => {
    render(<OverviewView result={sample()} gateProvenance={demo.provenance} {...callbacks}/>);
    const gate = screen.getByRole('region', { name: '当前模型质量门禁' });
    const metrics = screen.getByRole('region', { name: '评测核心指标' });
    expect(gate.nextElementSibling).toBe(metrics);
    expect(within(gate).getByRole('heading', { name: '当前模型质量门禁', level: 2 })).toBeInTheDocument();
    expect(within(gate).getByRole('status')).toHaveAttribute('aria-live', 'polite');
    expect(metrics.querySelectorAll('.metric-tile')).toHaveLength(4);
    expect(metrics.querySelector('.quality-gate')).toBeNull();
    expect(within(gate).getByText('本快照用于展示评测流程；PASS 仅表示指定正向样本达到现有门槛，不代表模型总体可靠性。')).toBeVisible();
    const details = gate.querySelector('details')!;
    expect(details).not.toHaveAttribute('open');
    expect(within(details).getByText(selectionNote)).not.toBeVisible();
    await userEvent.click(within(details).getByText('门禁依据与原始记录'));
    expect(details).toHaveAttribute('open');
    expect(within(details).getByText(selectionNote)).toBeVisible();
    expect(within(details).getByText('原始评测门禁：PASS')).toBeVisible();
  });

  it('shows the formal-run replay above the demo gate with its source, the Ragas-versus-Trace scope and the refusal note', () => {
    render(<OverviewView result={sample()} gateProvenance={demo.provenance} {...callbacks}/>);
    const replay = screen.getByRole('region', { name: '正式运行回放' });
    expect(replay.nextElementSibling).toBe(screen.getByRole('region', { name: '当前模型质量门禁' }));
    expect(within(replay).getByText('来源：25 题正式运行 · 请求配置 deepseek-chat · 20261005T144504Z（冻结证据，非本页演示快照）')).toBeInTheDocument();
    expect(within(replay).getByText(/自研 Trace 不参与门禁计算/)).toBeInTheDocument();
    expect(within(replay).getByText('门禁 FAIL')).toBeInTheDocument();
    expect(within(replay).getByText('正向忠实度 73.8% · 门槛 ≥ 80.0%')).toBeInTheDocument();
    expect(within(replay).getByText('4 题拒答拉低了均值；上下文精度 98.3% 达标。')).toBeInTheDocument();
    // Point → line → surface: 20 scored questions, 4 refusals drawn at 0, then the mean against the threshold.
    expect(within(replay).getByRole('img', { name: '20 道正向题的得分：4 题拒答按 0 计，均值 0.7375，低于门槛 0.8' })).toBeInTheDocument();
    expect(replay.querySelectorAll('.fr-dot')).toHaveLength(20);
    expect(replay.querySelectorAll('.fr-dot--refusal')).toHaveLength(4);
    expect(within(replay).getByRole('list', { name: '回放步骤' })).toHaveTextContent('点逐题得分线按题连接面门槛差距');
    // jsdom has no IntersectionObserver: the final frame is shown without animation classes.
    expect(replay).not.toHaveClass('replay-animated');
  });

  it('does not show the formal-run replay next to a live result', () => {
    render(<OverviewView result={sample()} {...callbacks}/>);
    expect(screen.queryByRole('region', { name: '正式运行回放' })).not.toBeInTheDocument();
  });

  it('settles the gate once per result and model, and states divergence from the original gate and the traced sentence base', async () => {
    render(<App/>);
    await screen.findByText('服务可连接');
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    const gate = () => screen.getByRole('region', { name: '当前模型质量门禁' });
    const tileNote = () => screen.getByText('幻觉语句数').closest('.metric-tile')!.querySelector('small')!.textContent;
    expect(gate()).toHaveClass('gate-settle-a');
    expect(gate().querySelector('.gate-divergence')).toBeNull();
    expect(tileNote()).toBe('共核对 5 句陈述');
    const selector = screen.getByRole('combobox', { name: '当前摘要模型' });
    await userEvent.selectOptions(selector, summaryModels[1].id);
    expect(gate()).toHaveClass('gate-settle-b');
    expect(gate().querySelector('.gate-divergence')).toHaveTextContent('原始门禁为 PASS，按首个有效模型判定，与当前模型结论不同。');
    await userEvent.selectOptions(selector, summaryModels[0].id);
    expect(gate()).toHaveClass('gate-settle-b');
    await userEvent.selectOptions(selector, summaryModels[2].id);
    expect(gate()).toHaveClass('gate-settle-a');
    expect(tileNote()).toBe('共核对 9 句陈述');
  });

  it('checks each demo model using positive metrics and stored thresholds without overwriting the original PASS or posting', async () => {
    const original = structuredClone(demo);
    render(<App/>);
    await screen.findByText('服务可连接');
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    const requestsBeforeSelection = requests.mock.calls.length;
    for (const model of summaryModels) {
      await userEvent.selectOptions(screen.getByRole('combobox', { name: '当前摘要模型' }), model.id);
      const gate = screen.getByText(model.gate).closest('.quality-gate')!;
      expect(within(gate as HTMLElement).getByText('当前模型质量门禁')).toBeInTheDocument();
      expect(within(gate as HTMLElement).getByText('本快照用于展示评测流程；PASS 仅表示指定正向样本达到现有门槛，不代表模型总体可靠性。')).toBeInTheDocument();
      expectGateContext(model.label, '3 条正向用例', model.label === 'Qwen2.5-7B' ? '33.3%' : '100.0%', '100.0%');
      expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
      expectSummaryValues(model.values);
    }
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '当前摘要模型' }), summaryModels[1].id);
    expect(demo.result.summary[1]).toMatchObject({ faithfulness: 0.25, answer_correctness: 0.2527, context_recall: 0.3333 });
    expect(screen.getByText('正向忠实度低于门槛')).toBeInTheDocument();
    expect(screen.getByText(selectionNote)).toBeInTheDocument();
    expect(screen.getByText(chartNote)).toBeInTheDocument();
    expect(screen.getByText(rule)).toBeInTheDocument();
    expect(screen.getByText('原始门禁说明未记录。')).toBeInTheDocument();
    expect(screen.getByText('已有阈值来自演示快照的原始运行记录；缺失项不作推断。')).toBeInTheDocument();
    expect(requests).toHaveBeenCalledTimes(requestsBeforeSelection);
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
    expect(demo).toEqual(original);
  });

  it.each([true, false])('preserves original gate=%s and warns rather than guessing when positive metrics or thresholds are unavailable', async (gatePassed) => {
    const result = sample();
    result.gate_passed = gatePassed;
    result.gate_message = '';
    result.summary.forEach((row) => {
      row.faithfulness = null;
      delete row.pos_faithfulness;
      delete row.pos_context_precision;
    });
    const original = structuredClone(result);
    const { rerender } = render(<OverviewView result={result} {...callbacks}/>);
    expect(screen.getByRole('combobox', { name: '当前摘要模型' })).toHaveValue('');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '当前摘要模型' }), summaryModels[1].id);
    const gate = screen.getByText('WARNING').closest('.quality-gate')!;
    expect(within(gate as HTMLElement).getByText(interpretation)).toBeInTheDocument();
    expectGateContext('Qwen2.5-7B', '3 条正向用例', '暂无数据', '暂无数据', '门槛未提供', '门槛未提供');
    expect(screen.getByText(`原始评测门禁：${gatePassed ? 'PASS' : 'FAIL'}`)).toBeInTheDocument();
    expect(screen.getByText('正向指标或阈值缺失，无法完成检查')).toBeInTheDocument();
    expect(screen.getByText('原始门禁说明未记录。')).toBeInTheDocument();
    expect(screen.getByText('未随结果返回的运行阈值不作推断。')).toBeInTheDocument();
    expect(screen.getByText(rule)).toBeInTheDocument();
    const details = document.querySelector('.gate-details')!;
    for (const label of ['正向忠实度', '正向上下文精度']) {
      expect(within(details as HTMLElement).getByText(label).closest('div')?.querySelector('dd')).toHaveTextContent('暂无数据');
    }
    expect(result).toEqual(original);
    const returnedMessage = '后端记录的门禁说明：保留原文，不重新推断。';
    rerender(<OverviewView result={{ ...result, gate_message: returnedMessage }} {...callbacks}/>);
    expect(screen.getByText(returnedMessage)).toBeInTheDocument();
    expect(screen.queryByText('原始门禁说明未记录。')).not.toBeInTheDocument();
    expect(screen.getByText('WARNING')).toBeInTheDocument();
    expect(screen.getByText(`原始评测门禁：${gatePassed ? 'PASS' : 'FAIL'}`)).toBeInTheDocument();
  });

  it.each([
    { faith: 0.8, context: 0.8, gate: 'PASS', reason: '两项正向指标均达到门槛' },
    { faith: 0.79, context: 0.8, gate: 'FAIL', reason: '正向忠实度低于门槛' },
    { faith: 0.8, context: 0.79, gate: 'FAIL', reason: '正向上下文精度低于门槛' },
    { faith: 0.79, context: 0.79, gate: 'FAIL', reason: '正向忠实度、正向上下文精度低于门槛' },
  ])('checks positive boundaries and explains $faith / $context as $gate', ({ faith, context, gate, reason }) => {
    const result = sample();
    result.summary[0].faithfulness = 0;
    result.summary[0].context_precision = 0;
    result.summary[0].pos_faithfulness = faith;
    result.summary[0].pos_context_precision = context;
    const original = structuredClone(result);
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    expect(screen.getByText(gate)).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', `${(faith * 100).toFixed(1)}%`, `${(context * 100).toFixed(1)}%`);
    expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
    expect(result).toEqual(original);
  });

  it.each([
    { id: summaryModels[1].id, label: 'Qwen2.5-7B', faith: 1, gate: 'PASS' },
    { id: summaryModels[0].id, label: 'DeepSeek-V4.1-Flash', faith: 0.2, gate: 'FAIL' },
  ])('uses values rather than model identity for $label → $gate', ({ id, label, faith, gate }) => {
    const result = sample();
    const row = result.summary.find((entry) => entry.model === id)!;
    row.pos_faithfulness = faith;
    row.pos_context_precision = 1;
    result.summary = [row];
    result.summary_df = [structuredClone(row)];
    result.gate_passed = false;
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    expect(screen.getByText(gate)).toBeInTheDocument();
    expectGateContext(label, '3 条正向用例', `${faith * 100}.0%`, '100.0%');
    expect(screen.getByText('原始评测门禁：FAIL')).toBeInTheDocument();
    expect(result.gate_passed).toBe(false);
  });

  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY])('does not replace missing positive faithfulness %s with overall faithfulness', (missing) => {
    const result = sample();
    if (missing === undefined) delete result.summary[0].pos_faithfulness;
    else result.summary[0].pos_faithfulness = missing;
    delete result.model_dfs[summaryModels[0].id];
    const original = structuredClone(result);
    render(<OverviewView result={result} gateProvenance={demo.provenance} {...callbacks}/>);
    expect(screen.getByText('WARNING')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '正向用例 · 数量未返回', '暂无数据', '100.0%');
    expect(screen.getByText('正向指标或阈值缺失，无法完成检查')).toBeInTheDocument();
    expect(result).toEqual(original);
  });

  it('keeps a snapshot threshold and positive zero while treating another missing threshold as unknown', () => {
    const result = sample();
    result.summary[0].pos_faithfulness = 0;
    result.summary[0].pos_context_precision = 0;
    const provenance: DemoFixture['provenance'] = structuredClone(demo.provenance);
    provenance.run = { thresholds: { faithfulness: 0, context_precision: null } };
    const originals = structuredClone({ result, provenance });
    render(<OverviewView result={result} gateProvenance={provenance} {...callbacks}/>);
    expect(screen.getByText('WARNING')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '0.0%', '0.0%', '门槛 ≥ 0.0%', '门槛未提供');
    expect(screen.getByText('已有阈值来自演示快照的原始运行记录；缺失项不作推断。')).toBeInTheDocument();
    expect({ result, provenance }).toEqual(originals);
  });

  it('passes when both positive values equal explicit zero thresholds without overwriting the original FAIL', () => {
    const result = sample();
    result.gate_passed = false;
    result.summary[0].pos_faithfulness = 0;
    result.summary[0].pos_context_precision = 0;
    const submittedConfig = { ...initialConfig, useDefaultThresholds: false, faithThreshold: 0, contextThreshold: 0, modelNames: [...initialConfig.modelNames] };
    const original = structuredClone({ result, submittedConfig });
    render(<OverviewView result={result} gateRequest={submittedConfig} {...callbacks}/>);
    expect(screen.getByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('两项正向指标均达到门槛')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '0.0%', '0.0%', '门槛 ≥ 0.0%', '门槛 ≥ 0.0%');
    expect(screen.getByText('原始评测门禁：FAIL')).toBeInTheDocument();
    expect({ result, submittedConfig }).toEqual(original);
  });

  it('warns for the retained completed live result during a different submission and after that submission fails', async () => {
    let finish!: (value: Response) => void;
    let evaluations = 0;
    const firstResult = sample();
    const originalResult = structuredClone(firstResult);
    requests.mockImplementation((url: string) => {
      if (url === '/api/health') return Promise.resolve(response({ status: 'ok' }));
      // Opening the workspace also reads the registered models; only evaluations are counted.
      if (url === '/api/models') return Promise.resolve(response({ models: [] }));
      evaluations += 1;
      return evaluations === 1 ? Promise.resolve(response(firstResult)) : new Promise<Response>((resolve) => { finish = resolve; });
    });
    render(<App/>);
    await screen.findByText('服务可连接');
    await openWorkspace();
    await userEvent.click(screen.getByRole('checkbox', { name: '沿用后端默认阈值' }));
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('真实评测结果');
    expect(screen.getByText('PASS')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%');
    await openWorkspace();
    fireEvent.change(screen.getByRole('spinbutton', { name: '忠实度门禁阈值' }), { target: { value: '0.9' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: '上下文精度门禁阈值' }), { target: { value: '0.95' } });
    await userEvent.click(screen.getByRole('button', { name: '再次评测' }));
    await userEvent.click(screen.getByRole('button', { name: '评测总览' }));
    expect(screen.getByText('WARNING')).toBeInTheDocument();
    expect(screen.getByText('上次结果 · 本次尚未更新')).toBeInTheDocument();
    expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%', '门槛未提供', '门槛未提供');
    await act(async () => { finish(response({}, 500)); });
    await screen.findByText('本次评测未完成');
    expect(screen.getByText('WARNING')).toBeInTheDocument();
    expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%', '门槛未提供', '门槛未提供');
    const calls = requests.mock.calls.filter(([url]) => url === '/api/evaluate');
    expect(calls).toHaveLength(2);
    expect(JSON.parse(calls[0][1].body)).toMatchObject({ faith_threshold: 0.8, ctx_prec_threshold: 0.8 });
    expect(JSON.parse(calls[1][1].body)).toMatchObject({ faith_threshold: 0.9, ctx_prec_threshold: 0.95 });
    expect(firstResult).toEqual(originalResult);
  });

  it('never applies a new submission or edited form thresholds to the previous result', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url === '/api/health' ? Promise.resolve(response({ status: 'ok' })) : new Promise<Response>((resolve) => { finish = resolve; }));
    render(<App/>);
    await screen.findByText('服务可连接');
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    await openWorkspace();
    await userEvent.click(screen.getByRole('checkbox', { name: '沿用后端默认阈值' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: '忠实度门禁阈值' }), { target: { value: '0' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: '上下文精度门禁阈值' }), { target: { value: '0' } });
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await userEvent.click(screen.getByRole('button', { name: '评测总览' }));
    expect(screen.getByText('上次结果 · 本次尚未更新')).toBeInTheDocument();
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%');
    const completed = sample();
    completed.gate_passed = false;
    await act(async () => { finish(response(completed)); });
    await screen.findByText('真实评测结果');
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%', '门槛 ≥ 0.0%', '门槛 ≥ 0.0%');
    expect(screen.getByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('原始评测门禁：FAIL')).toBeInTheDocument();
    await openWorkspace();
    fireEvent.change(screen.getByRole('spinbutton', { name: '忠实度门禁阈值' }), { target: { value: '0.6' } });
    await userEvent.click(screen.getByRole('button', { name: '评测总览' }));
    expectGateContext('DeepSeek-V4.1-Flash', '3 条正向用例', '100.0%', '100.0%', '门槛 ≥ 0.0%', '门槛 ≥ 0.0%');
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });
});

describe('configuration check feedback', () => {
  async function failedEvaluation() {
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : response({}, 500)));
    render(<App/>);
    await screen.findByText('服务可连接');
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('本次评测未完成');
  }

  it.each(['workspace', 'overview'])('checks parameters and health from %s without resubmitting', async (view) => {
    await failedEvaluation();
    if (view === 'overview') await userEvent.click(screen.getByRole('button', { name: '评测总览' }));
    const healthCalls = requests.mock.calls.filter(([url]) => url === '/api/health').length;
    await userEvent.click(screen.getByRole('button', { name: '检查配置' }));
    const feedback = await screen.findByRole('status', { name: '配置检查结果' });
    await waitFor(() => expect(feedback).toHaveTextContent('本地参数检查通过，服务可连接。'));
    expect(feedback).toHaveTextContent('不会验证文件是否存在、模型或凭据是否可用');
    expect(feedback).toHaveFocus();
    expect(screen.getByRole('heading', { name: '评测配置' })).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/health')).toHaveLength(healthCalls + 1);
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
  });

  it('shows loading, prevents duplicate checks, reports disconnection and allows a fresh check', async () => {
    await failedEvaluation();
    let finish!: (value: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const healthCalls = requests.mock.calls.filter(([url]) => url === '/api/health').length;
    const button = screen.getByRole('button', { name: '检查配置' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.getByRole('button', { name: '检查中…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    expect(screen.getByRole('status', { name: '配置检查结果' })).toHaveTextContent('正在校验本地参数');
    expect(requests.mock.calls.filter(([url]) => url === '/api/health')).toHaveLength(healthCalls + 1);
    await act(async () => { finish(response({}, 503)); });
    expect(screen.getByRole('status', { name: '配置检查结果' })).toHaveTextContent('服务未连接');
    expect(screen.getByRole('button', { name: '检查配置' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '开始评测' })).toBeEnabled();
    requests.mockResolvedValue(response({ status: 'ok' }));
    await userEvent.click(screen.getByRole('button', { name: '检查配置' }));
    await waitFor(() => expect(screen.getByRole('status', { name: '配置检查结果' })).toHaveTextContent('服务可连接'));
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });

  it('reports invalid parameters before requesting health and clears outdated feedback on editing', async () => {
    await failedEvaluation();
    for (const label of ['选择 DeepSeek-V4.1-Flash', '选择 Qwen2.5-7B', '选择 GLM-5.2-Fast']) await userEvent.click(screen.getByRole('button', { name: label }));
    const healthCalls = requests.mock.calls.filter(([url]) => url === '/api/health').length;
    await userEvent.click(screen.getByRole('button', { name: '检查配置' }));
    expect(screen.getByRole('status', { name: '配置检查结果' })).toHaveTextContent('配置未通过本地检查');
    expect(screen.getByText('请至少选择一个模型。')).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/health')).toHaveLength(healthCalls);
    await userEvent.click(screen.getByRole('button', { name: '选择 DeepSeek-V4.1-Flash' }));
    expect(screen.queryByRole('status', { name: '配置检查结果' })).not.toBeInTheDocument();
    expect(screen.queryByText('请至少选择一个模型。')).not.toBeInTheDocument();
  });

  it('service refresh has a visible busy state and disables repeated clicks', async () => {
    render(<App/>);
    await screen.findByText('服务可连接');
    let finish!: (value: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const button = screen.getByRole('button', { name: '重新检查服务连接' });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    expect(screen.getByText('检查服务连接')).toBeInTheDocument();
    await act(async () => { finish(response({}, 503)); });
    expect(button).toBeEnabled();
    expect(screen.getByText('服务未连接')).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
  });

  it('disables the empty-state demo action while the first evaluation is running', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url === '/api/health' ? Promise.resolve(response({ status: 'ok' })) : new Promise<Response>((resolve) => { finish = resolve; }));
    render(<App/>);
    await openWorkspace();
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await userEvent.click(screen.getByRole('button', { name: '评测总览' }));
    expect(screen.getByRole('button', { name: '查看演示结果' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '加载演示' })).toBeDisabled();
    await act(async () => { finish(response(sample())); });
    await screen.findByText('WARNING');
    expect(screen.getByText('原始评测门禁：PASS')).toBeInTheDocument();
  });
});


describe('Verification navigation regression', () => {
  it('adds a sixth read-only view without evaluating or changing the existing demo', async () => {
    const { webcrypto } = await import('node:crypto');
    vi.stubGlobal('crypto', webcrypto);
    render(<App/>);
    const nav = screen.getByRole('navigation', { name: '工作区导航' });
    expect(within(nav).getAllByRole('button').map(button => button.textContent)).toEqual(['评测总览', '评测工作区', '模型配置', '多模型对比', '幻觉分析', '评测报告', '评测验证', '门禁审计', '修复验证', '鲁棒性体检', '盲区体检', '人工标注', '父条资料准备']);
    await userEvent.click(within(nav).getByRole('button', { name: '评测验证' }));
    expect(screen.getByText('尚未加载可核验结果')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    await userEvent.click(within(nav).getByRole('button', { name: '评测验证' }));
    await screen.findByRole('table');
    const scope = screen.getByRole('region', { name: '人工核验范围' });
    expect([...scope.querySelectorAll('dt')].map(element => element.textContent)).toEqual(['已检查', '待核验', '未纳入']);
    const counts = [...scope.querySelectorAll('dd')].map(element => Number.parseInt(element.textContent ?? '', 10));
    expect(counts[0] + counts[1]).toBe(13);
    expect(counts[2]).toBe(5);
    for (const label of ['评测总览', '评测工作区', '多模型对比', '幻觉分析', '评测报告']) {
      await userEvent.click(within(nav).getByRole('button', { name: label }));
      expect(screen.getByRole('heading', { level: 1, name: label })).toBeVisible();
    }
    await userEvent.click(within(nav).getByRole('button', { name: '门禁审计' }));
    expect(screen.getByRole('heading', { level: 1, name: '门禁审计' })).toBeVisible();
    expect(screen.getByRole('region', { name: '门禁可信度四问' })).toBeVisible();
    // The audit reads frozen evidence only, so the loaded demo's toolbar and provenance stay out of it.
    expect(screen.queryByText('演示快照', { selector: '.badge' })).not.toBeInTheDocument();
    expect(screen.queryByText('查看演示数据来源')).not.toBeInTheDocument();
    await userEvent.click(within(nav).getByRole('button', { name: '评测总览' }));
    expect(screen.getByRole('region', { name: '当前模型质量门禁' })).toHaveTextContent('PASS');
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
  });
});
