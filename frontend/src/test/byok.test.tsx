import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { byokEntries, byokRequestModels, removeByokModel, saveByokModel, validateByokModel } from '../api/byok';
import { evaluate } from '../api/client';
import { downloadEvaluationRecord, downloadRecordJson } from '../api/evaluations';
import { importInput } from '../api/inputFiles';
import { sessionHeaders } from '../api/session';
import { createImprovement, downloadImprovement, verifyImprovement } from '../api/answerImprovement';
import { webcrypto } from 'node:crypto';
import { ModelsView } from '../features/ModelsView';
import { WorkspaceView } from '../features/WorkspaceView';
import { initialConfig, runReducer, initialRunState, toRequest, validateConfig } from '../state';
import type { EvaluationRecord } from '../api/types';
import demo from '../fixtures/demo.json';
import App from '../App';

vi.mock('../config', async (original) => ({ ...await original<typeof import('../config')>(), BYOK: true, READ_ONLY: false, STATIC_SITE: false }));
vi.mock('../components/Charts', () => ({ AbilityChart: () => <div/>, RiskChart: () => <div/>, DurationChart: () => <div/> }));

const key = 'sk-test-private-credential';
const model = { name: 'deepseek-chat', base_url: 'https://api.deepseek.com', api_key: key };
const id = 'a'.repeat(32);
const config = { ...initialConfig, modelNames: ['deepseek-chat'] };
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let requests: ReturnType<typeof vi.fn>;
beforeEach(() => { byokEntries().forEach((entry) => removeByokModel(entry.name)); requests = vi.fn(); vi.stubGlobal('fetch', requests); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('BYOK cloud credentials and selection', () => {
  it.each([
    [{ ...model, name: '' }, '模型名'],
    [{ ...model, api_key: '' }, 'API Key'],
    [{ ...model, api_key: 'short' }, 'API Key'],
    [{ ...model, api_key: 'secret key' }, 'API Key'],
    [{ ...model, base_url: 'http://127.0.0.1/v1' }, 'HTTPS'],
    [{ ...model, base_url: 'https://api.deepseek.com?key=secret' }, '不能包含'],
  ])('blocks invalid configuration %o', (value, problem) => { expect(validateByokModel(value, [])).toContain(problem); expect(() => saveByokModel(value)).toThrow(problem); });

  it('keeps keys out of storage, public entries, retained result config and exported records', async () => {
    const stored = vi.spyOn(Storage.prototype, 'setItem');
    const list = saveByokModel(model);
    expect(stored).not.toHaveBeenCalled();
    expect(JSON.stringify(list)).not.toContain(key);
    expect(byokRequestModels(['deepseek-chat'])).toEqual([model]);
    expect(() => saveByokModel(model)).toThrow('已存在');
    const state = runReducer(runReducer(initialRunState, { type: 'start', config }), { type: 'complete', result: demo.result });
    expect(JSON.stringify(state)).not.toContain(key);
    const record: EvaluationRecord = { schema_version: 1, id, created_at: '2026-11-02', source: 'evaluation', request: toRequest(config), result: demo.result, report_html: null, chart_png_base64: null, artifact_warnings: [] };
    let content: Blob | undefined;
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn((blob: Blob) => { content = blob; return 'blob:test'; }), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    downloadRecordJson(record);
    const text = await new Promise<string>((resolve) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(content!); });
    expect(text).not.toContain(key); expect(text).not.toContain('byok_models');
    removeByokModel(model.name); expect(() => byokRequestModels(['deepseek-chat'])).toThrow('重新填写');
  });

  it('starts empty, excludes author models and TruthfulQA in the cloud workspace', () => {
    expect(initialConfig.modelNames).toEqual([]);
    expect(validateConfig({ ...config, testsetMode: 'benchmark' })).toContain('未开放');
    render(<WorkspaceView config={initialConfig} onChange={vi.fn()} onRun={vi.fn()} running={false} hasLiveResult={false} error={null} configurationCheck={null}/>);
    expect(screen.queryByRole('button', { name: /选择 DeepSeek/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /TruthfulQA/ })).not.toBeInTheDocument();
    expect(screen.getByText(/还没有配置模型/)).toBeInTheDocument();
  });

  it('supports provider shortcuts and password-only credential entry without requesting server model configuration', async () => {
    const changed = vi.fn(); render(<ModelsView onModelsChange={changed}/>);
    expect(screen.queryByText('本机登记的模型')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '配置模型' }));
    const form = screen.getByRole('form', { name: '配置云端模型' });
    expect(within(form).getByLabelText('API Key')).toHaveAttribute('type', 'password');
    expect(within(form).getByLabelText('接口地址')).toHaveValue('https://api.deepseek.com');
    await userEvent.selectOptions(within(form).getByLabelText('接口服务'), 'https://dashscope.aliyuncs.com/compatible-mode/v1');
    expect(within(form).getByLabelText('模型名')).toHaveValue('qwen-flash');
    await userEvent.click(within(form).getByRole('button', { name: '保存到本页' }));
    expect(within(form).getByRole('alert')).toHaveTextContent('API Key');
    fireEvent.change(within(form).getByLabelText('API Key'), { target: { value: key } });
    await userEvent.click(within(form).getByRole('button', { name: '保存到本页' }));
    expect(screen.getByText('已填写')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('尚未验证接口可用');
    expect(document.body.textContent).not.toContain(key);
    expect(changed).toHaveBeenCalledWith(expect.arrayContaining([expect.objectContaining({ name: 'qwen-flash' })]));
    expect(requests).not.toHaveBeenCalled();
  });

  it('includes an independently configured judge and enforces three total unique models', () => {
    saveByokModel(model); saveByokModel({ ...model, name: 'judge' }); saveByokModel({ ...model, name: 'third' });
    expect(byokRequestModels(['deepseek-chat'], 'judge').map((entry) => entry.name)).toEqual(['deepseek-chat', 'judge']);
    expect(() => saveByokModel({ ...model, name: 'fourth' })).toThrow('最多');
  });
});

describe('BYOK single POST and session isolation', () => {
  it('shows actual cloud job progress and finishes in the normal result view', async () => {
    saveByokModel(model);
    requests.mockImplementation((url: string) => Promise.resolve(url === '/api/health' ? response({ status: 'ok' }) : url === '/api/evaluate' ? response({ job_id: id }, 202) : response({ status: 'running', step: 2, message: '正在读取你的资料' })));
    render(<App/>);
    await userEvent.click(screen.getByRole('button', { name: '新建评测' }));
    expect(screen.getByRole('button', { name: '选择 deepseek-chat' })).toHaveAttribute('aria-pressed', 'true');
    expect(requests.mock.calls.some(([url]) => url === '/api/models')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: '开始评测' }));
    expect(screen.getByRole('progressbar', { name: '评测进度' })).not.toHaveAttribute('aria-valuenow');
    expect(await screen.findByText('正在读取你的资料', {}, { timeout: 3500 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '评测中' })).toBeDisabled();
    requests.mockImplementation((url: string) => Promise.resolve(url.startsWith('/api/jobs/') ? response({ status: 'completed', step: 5, message: '完成', result: demo.result }) : response({ status: 'ok' })));
    expect(await screen.findByText('真实评测结果', {}, { timeout: 3500 })).toBeInTheDocument();
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  }, 10000);

  it('keeps a stable cryptographic session when browser storage is disabled', async () => {
    vi.resetModules();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('disabled'); });
    const isolated = await import('../api/session');
    const first = isolated.sessionHeaders()['X-VeriScope-Session'];
    expect(first).toMatch(/^[a-f0-9]{64}$/); expect(isolated.sessionHeaders()['X-VeriScope-Session']).toBe(first);
  });

  it('polls real progress then returns a completed result without another paid POST', async () => {
    vi.useFakeTimers(); saveByokModel(model);
    requests.mockResolvedValueOnce(response({ job_id: id }, 202)).mockResolvedValueOnce(response({ status: 'running', step: 2, message: '正在生成回答' })).mockResolvedValueOnce(response({ status: 'completed', step: 5, message: '已完成', result: demo.result }));
    const progress = vi.fn(); const pending = evaluate(toRequest(config), progress);
    await vi.advanceTimersByTimeAsync(4000);
    await expect(pending).resolves.toEqual(demo.result);
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(JSON.parse(requests.mock.calls[0][1].body).byok_models).toEqual([model]);
    expect(requests.mock.calls[1][0]).toBe(`/api/jobs/${id}`);
    expect(progress).toHaveBeenCalledWith('正在生成回答');
    expect(requests.mock.calls.every(([, init]) => init?.headers['X-VeriScope-Session'] === sessionHeaders()['X-VeriScope-Session'])).toBe(true);
  });

  it.each(['failed', 'connection'])('does not resubmit after %s while polling', async (failure) => {
    vi.useFakeTimers(); saveByokModel(model); requests.mockResolvedValueOnce(response({ job_id: id }, 202));
    if (failure === 'failed') requests.mockResolvedValueOnce(response({ status: 'failed', step: 3, message: '评测失败', error: `额度不足 ${key}` }));
    else requests.mockRejectedValueOnce(new TypeError('offline'));
    const outcome = evaluate(toRequest(config)).then(() => null, (error: Error) => error);
    await vi.advanceTimersByTimeAsync(2000); const error = await outcome;
    expect(error).toBeInstanceOf(Error); expect(error?.message).not.toContain(key);
    expect(requests.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(10000); expect(requests).toHaveBeenCalledTimes(2);
  });

  it('reports controlled busy / allowlist errors without retrying or exposing keys', async () => {
    saveByokModel(model);
    requests.mockResolvedValueOnce(response({ detail: '已有评测运行，请等待。' }, 409));
    await expect(evaluate(toRequest(config))).rejects.toThrow('已有评测'); expect(requests).toHaveBeenCalledTimes(1);
    requests.mockResolvedValueOnce(response({ detail: '自定义接口未加入允许列表。' }, 422));
    await expect(evaluate(toRequest(config))).rejects.toThrow('允许列表'); expect(requests).toHaveBeenCalledTimes(2);
  });

  it('attaches the same session to file upload and record downloads', async () => {
    const file = new File(['document'], 'visitor.txt');
    requests.mockResolvedValueOnce(response({ kind: 'document', file_name: file.name, relative_path: `work/uploads/${id}.txt`, size_bytes: file.size }));
    await importInput(file, 'document');
    const record: EvaluationRecord = { schema_version: 1, id, created_at: '2026-11-02', source: 'evaluation', request: toRequest(config), result: demo.result, report_html: null, chart_png_base64: null, artifact_warnings: [] };
    requests.mockResolvedValueOnce(response(record)); await downloadEvaluationRecord(id);
    expect(requests.mock.calls.every(([, init]) => init?.headers['X-VeriScope-Session'] === sessionHeaders()['X-VeriScope-Session'])).toBe(true);
  });

  it('keeps numeric candidate creation, verification and byte-preserving download in the same session', async () => {
    const improvementId = 'b'.repeat(32);
    const candidate = { schema_version: 1, id: improvementId, parent_record_id: id, model_name: model.name,
      scope: 'single-quantity-literal-v1', original_gate: { passed: false, message: '原始失败' }, bindings: {},
      counts: { total_rows: 0, changed_rows: 0, verified_rows: 0, no_candidate_rows: 0, review_rows: 0 },
      items: [], rescoring: { status: 'not_run', result: null } };
    requests.mockResolvedValueOnce(response(candidate)).mockResolvedValueOnce(response({ schema_version: 1,
      improvement_id: improvementId, status: 'verified', checks: [], items: [], semantic_evaluation: 'NOT_EVALUATED',
      rescoring: { status: 'not_run', result: null } })).mockResolvedValueOnce(response(candidate));
    vi.stubGlobal('crypto', webcrypto);
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:candidate'), revokeObjectURL: vi.fn() }));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await createImprovement(id, model.name);
    await verifyImprovement(improvementId);
    expect(await downloadImprovement(improvementId)).toEqual({ sha256: expect.stringMatching(/^[a-f0-9]{64}$/), size: expect.any(Number) });
    expect(requests.mock.calls.map(([url]) => url)).toEqual([
      `/api/evaluations/${id}/improvements`, `/api/improvements/${improvementId}/verify`, `/api/improvements/${improvementId}`,
    ]);
    expect(requests.mock.calls.every(([, init]) => init?.headers['X-VeriScope-Session'] === sessionHeaders()['X-VeriScope-Session'])).toBe(true);
    expect(JSON.parse(requests.mock.calls[0][1].body)).toEqual({ model_name: model.name });
    expect(JSON.stringify(requests.mock.calls)).not.toContain(key);
  });
});
