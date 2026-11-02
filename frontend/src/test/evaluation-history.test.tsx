import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { ReportView } from '../features/ReportView';
import type { EvaluationResult } from '../api/types';
import demo from '../fixtures/demo.json';

vi.mock('../components/Charts', () => ({
  AbilityChart: () => <div/>, RiskChart: () => <div/>, DurationChart: () => <div/>,
}));

const id = 'a'.repeat(32);
const otherId = 'b'.repeat(32);
const sample = (): EvaluationResult => structuredClone(demo.result) as EvaluationResult;
const request = { doc_path: 'scenarios/archived.txt', testset_path: 'fixed.csv', benchmark: null, benchmark_limit: 7, model_names: ['deepseek-chat'], judge_model_name: 'qwen-flash', faith_threshold: 0, ctx_prec_threshold: 0.7, version_label: null };
const record = () => ({ schema_version: 1, id, created_at: '2026-11-02T12:00:00Z', source: 'evaluation', request, result: sample(), report_html: '<h1>归档报告</h1>', chart_png_base64: null, artifact_warnings: [] });
const listed = () => ({ records: [{ id, created_at: '2026-11-02T12:00:00Z', source: 'evaluation', document_name: 'archived.txt', models: ['deepseek-chat'], gate_passed: true, total_elapsed_seconds: 12, has_report: true, artifact_warnings: [] }], unreadable_count: 0 });
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let requests: ReturnType<typeof vi.fn>;
let downloaded: Blob | null;

beforeEach(() => {
  downloaded = null;
  requests = vi.fn().mockImplementation((url: string) => Promise.resolve(
    url === '/api/health' ? response({ status: 'ok' })
      : url === '/api/models' ? response({ models: [] })
        : url === '/api/evaluations' ? response(listed())
          : url === `/api/evaluations/${id}/report` ? new Response('<h1>归档报告</h1>')
            : url === `/api/evaluations/${id}` || url === `/api/evaluations/${id}/download` ? response(record())
              : response({ ...sample(), record_id: id, saved_at: '2026-11-02T12:00:00Z' })));
  vi.stubGlobal('fetch', requests);
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn((blob: Blob) => { downloaded = blob; return 'blob:history-test'; }) });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); });

async function runEvaluation() {
  await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
  await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
  await screen.findByText('真实评测结果');
}

function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob); });
}

describe('评测记录的用户流程', () => {
  it('重复加载演示后可返回原评测，并恢复它的阈值与配置', async () => {
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
    await userEvent.click(screen.getByRole('checkbox', { name: '沿用后端默认阈值' }));
    fireEvent.change(screen.getByLabelText('忠实度门禁阈值'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('上下文精度门禁阈值'), { target: { value: '0.7' } });
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('真实评测结果');
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    expect(screen.getByRole('button', { name: '保存记录' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '返回评测结果' }));
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
    const gate = screen.getByLabelText('门禁检查上下文');
    expect(gate).toHaveTextContent('门槛 ≥ 0.0%');
    expect(gate).toHaveTextContent('门槛 ≥ 70.0%');
    await userEvent.click(screen.getByRole('button', { name: '评测工作区' }));
    expect(screen.getByLabelText('忠实度门禁阈值')).toHaveValue(0);
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });

  it('仅打开历史时读取列表，刷新后可恢复逐题记录和原参数并跨页查看', async () => {
    const page = render(<App/>);
    await screen.findByText('服务可连接');
    expect(requests.mock.calls.map(([url]) => url)).toEqual(['/api/health']);
    page.unmount();
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    const panel = screen.getByRole('region', { name: '历史记录' });
    await userEvent.click(await within(panel).findByRole('button', { name: /打开记录/ }));
    expect(await screen.findByText('历史评测记录')).toBeInTheDocument();
    expect(screen.queryByText('本次接口返回结果')).not.toBeInTheDocument();
    expect(screen.getByLabelText('门禁检查上下文')).toHaveTextContent('门槛 ≥ 0.0%');
    await userEvent.click(screen.getByRole('button', { name: '多模型对比' }));
    expect(screen.getByRole('table', { name: /各模型的全部原始指标/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '幻觉分析' }));
    expect(screen.getByRole('heading', { name: '逐句溯源' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '评测报告' }));
    expect(await screen.findByTitle('历史报告预览')).toHaveAttribute('sandbox', '');
    await userEvent.click(screen.getByRole('button', { name: '评测工作区' }));
    expect(screen.getByLabelText('评测文档相对路径')).toHaveValue('scenarios/archived.txt');
    expect(screen.getByLabelText('测试集来源')).toHaveValue('custom');
    expect(screen.getByLabelText('评测裁判')).toHaveValue('qwen-flash');
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(0);
  });

  it('下载服务中完整 JSON 包，保留报告并可从历史入口导回', async () => {
    render(<App/>);
    await runEvaluation();
    await userEvent.click(screen.getByRole('button', { name: '保存记录' }));
    await waitFor(() => expect(downloaded).not.toBeNull());
    const exported = JSON.parse(await blobText(downloaded!));
    expect(exported).toMatchObject({ id, report_html: '<h1>归档报告</h1>', request: { doc_path: 'scenarios/archived.txt' } });
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/evaluations/import'
      ? response({ ...record(), id: otherId, source: 'imported' })
      : url === '/api/evaluations' ? response(listed()) : response({ status: 'ok' })));
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    fireEvent.change(screen.getByLabelText('选择评测记录 JSON'), { target: { files: [new File([JSON.stringify(exported)], 'record.json', { type: 'application/json' })] } });
    expect(await screen.findByText('历史评测记录')).toBeInTheDocument();
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
    const imported = requests.mock.calls.find(([url]) => url === '/api/evaluations/import')!;
    expect(JSON.parse(imported[1].body).report_html).toBe('<h1>归档报告</h1>');
  });

  it('服务保存失败仍显示结果，下载含原请求的备份且不声称已存入历史', async () => {
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/evaluate'
      ? response({ ...sample(), record_id: null, save_error: '记录保存失败，请下载 JSON 备份。' })
      : url === '/api/health' ? response({ status: 'ok' }) : response({ models: [] })));
    render(<App/>);
    await runEvaluation();
    expect(screen.getByText(/记录保存失败/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '保存记录' }));
    await waitFor(() => expect(downloaded).not.toBeNull());
    const backup = JSON.parse(await blobText(downloaded!));
    expect(backup).toMatchObject({ schema_version: 1, request: { doc_path: 'demo.txt', faith_threshold: null }, result: { gate_passed: true }, report_html: null });
    const notice = screen.getByText(/已下载 JSON 备份/);
    expect(notice).toHaveAttribute('role', 'status');
    expect(notice).toHaveTextContent('未保存到服务历史');
    expect(requests.mock.calls.filter(([url]) => url === '/api/evaluate')).toHaveLength(1);
  });

  it('历史加载和导入失败时保留当前结果，显示错误并允许重试', async () => {
    render(<App/>);
    await runEvaluation();
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/evaluations' ? response(listed()) : response({}, 500)));
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    await userEvent.click(await screen.findByRole('button', { name: /打开记录/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('历史');
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /打开记录/ })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('选择评测记录 JSON'), { target: { files: [new File(['{broken'], 'bad.json')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('JSON');
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
  });

  it('列表损坏响应不会伪装成空历史，入口可用键盘打开关闭', async () => {
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/evaluations' ? response({ records: null }) : response({ status: 'ok' })));
    render(<App/>);
    const toggle = screen.getByRole('button', { name: '历史记录' });
    toggle.focus();
    await userEvent.keyboard('{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('历史');
    expect(screen.queryByText('尚无保存的记录')).not.toBeInTheDocument();
    const close = screen.getByRole('button', { name: '关闭历史记录' });
    close.focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.queryByRole('region', { name: '历史记录' })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });

  it('历史只返回一项显式阈值时另一项保持未知，不补初始默认值', async () => {
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' })
      : url === '/api/evaluations' ? response(listed())
        : response({ ...record(), request: { ...request, faith_threshold: null } })));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    await userEvent.click(await screen.findByRole('button', { name: /打开记录/ }));
    await screen.findByText('历史评测记录');
    const gate = screen.getByLabelText('门禁检查上下文');
    expect(gate.querySelector('[data-gate-field="pos_faithfulness"]')).toHaveTextContent('门槛未提供');
    expect(gate.querySelector('[data-gate-field="pos_context_precision"]')).toHaveTextContent('门槛 ≥ 70.0%');
    expect(screen.getByText('WARNING')).toBeInTheDocument();
  });

  it('从历史恢复的默认阈值保留空项，用户重新提交时仍交给服务决定', async () => {
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' })
      : url === '/api/evaluations' ? response(listed()) : url === '/api/models' ? response({ models: [] })
        : url === '/api/evaluate' ? response({ ...sample(), record_id: id })
          : response({ ...record(), request: { ...request, faith_threshold: null } })));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    await userEvent.click(await screen.findByRole('button', { name: /打开记录/ }));
    await screen.findByText('历史评测记录');
    await userEvent.click(screen.getByRole('button', { name: '评测工作区' }));
    expect(screen.getByLabelText('忠实度门禁阈值')).toHaveValue(null);
    expect(screen.getByLabelText('忠实度门禁阈值')).toHaveAttribute('placeholder', '后端默认');
    expect(screen.getByLabelText('上下文精度门禁阈值')).toHaveValue(0.7);
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('真实评测结果');
    const call = requests.mock.calls.find(([url]) => url === '/api/evaluate')!;
    expect(JSON.parse(call[1].body)).toMatchObject({ faith_threshold: null, ctx_prec_threshold: 0.7 });
  });

  it('打开历史的迟到响应不会覆盖用户新选择的演示', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url === `/api/evaluations/${id}`
      ? new Promise<Response>((resolve) => { finish = resolve; })
      : Promise.resolve(url === '/api/evaluations' ? response(listed()) : response({ status: 'ok' })));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    await userEvent.click(await screen.findByRole('button', { name: /打开记录/ }));
    await userEvent.click(screen.getByRole('button', { name: '加载演示' }));
    expect(screen.getByText('演示快照')).toBeInTheDocument();
    await act(async () => { finish(response(record())); });
    expect(screen.getByText('演示快照')).toBeInTheDocument();
    expect(screen.queryByText('历史评测记录')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '历史记录' })).not.toBeInTheDocument();
  });

  it('导入历史的迟到响应不会覆盖用户新完成的评测', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url === '/api/evaluations/import'
      ? new Promise<Response>((resolve) => { finish = resolve; })
      : Promise.resolve(url === '/api/health' ? response({ status: 'ok' })
        : url === '/api/models' ? response({ models: [] })
          : url === '/api/evaluations' ? response(listed()) : response({ ...sample(), record_id: otherId })));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    await screen.findByRole('button', { name: /打开记录/ });
    fireEvent.change(screen.getByLabelText('选择评测记录 JSON'), { target: { files: [new File([JSON.stringify(record())], 'record.json')] } });
    await waitFor(() => expect(requests.mock.calls.filter(([url]) => url === '/api/evaluations/import')).toHaveLength(1));
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await screen.findByText('真实评测结果');
    await act(async () => { finish(response(record())); });
    expect(screen.getByText('真实评测结果')).toBeInTheDocument();
    expect(screen.queryByText('历史评测记录')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '历史记录' })).not.toBeInTheDocument();
  });

  it('下一次评测失败后仍以历史结果绑定的原请求展示阈值', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url === '/api/evaluate'
      ? new Promise<Response>((resolve) => { finish = resolve; })
      : Promise.resolve(url === '/api/health' ? response({ status: 'ok' })
        : url === '/api/models' ? response({ models: [] })
          : url === '/api/evaluations' ? response(listed()) : response(record())));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '历史记录' }));
    await userEvent.click(await screen.findByRole('button', { name: /打开记录/ }));
    await screen.findByText('历史评测记录');
    await userEvent.click(screen.getByRole('button', { name: '评测工作区' }));
    fireEvent.change(screen.getByLabelText('忠实度门禁阈值'), { target: { value: '0.95' } });
    fireEvent.change(screen.getByLabelText('上下文精度门禁阈值'), { target: { value: '0.9' } });
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    await act(async () => { finish(response({}, 500)); });
    await screen.findByText('本次评测未完成');
    await userEvent.click(screen.getByRole('button', { name: '评测总览' }));
    const gate = screen.getByLabelText('门禁检查上下文');
    expect(gate).toHaveTextContent('门槛 ≥ 0.0%');
    expect(gate).toHaveTextContent('门槛 ≥ 70.0%');
    expect(screen.getByText('上次结果 · 本次尚未更新')).toBeInTheDocument();
    expect(screen.getByText('PASS')).toBeInTheDocument();
    expect(screen.getByText('历史评测记录')).toBeInTheDocument();
  });
});

describe('按记录绑定报告', () => {
  it('按 record_id 抓取报告并移除脚本和外部资源后隔离预览', async () => {
    requests.mockResolvedValue(new Response('<h1>已保存报告</h1><script>alert(1)</script><img src="https://external.invalid/a"><a href="https://external.invalid">外链</a>'));
    render(<ReportView result={{ ...sample(), record_id: id }}/>);
    const frame = await screen.findByTitle('历史报告预览');
    expect(requests).toHaveBeenCalledWith(`/api/evaluations/${id}/report`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(frame).toHaveAttribute('sandbox', '');
    const source = frame.getAttribute('srcdoc')!;
    expect(source).toContain('已保存报告');
    expect(source).toContain("default-src 'none'");
    expect(source).not.toContain('<script>');
    expect(source).not.toContain('https://external.invalid');
  });

  it('切换记录时不会显示迟到的旧报告，缺失报告如实提示', async () => {
    let finish!: (value: Response) => void;
    requests.mockImplementation((url: string) => url.includes(id) ? new Promise<Response>((resolve) => { finish = resolve; }) : Promise.resolve(response({}, 404)));
    const page = render(<ReportView result={{ ...sample(), record_id: id }}/>);
    await waitFor(() => expect(requests).toHaveBeenCalledTimes(1));
    page.rerender(<ReportView result={{ ...sample(), record_id: otherId }}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('报告');
    await act(async () => { finish(new Response('<h1>旧报告</h1>')); });
    expect(screen.queryByTitle('历史报告预览')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导入 HTML 报告' })).toBeEnabled();
  });
});
