import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchModels, saveLocalModels, suggestKeyEnv, validateLocalModel } from '../api/models';
import type { ModelEntry } from '../api/models';
import { ModelsView } from '../features/ModelsView';
import { WorkspaceView } from '../features/WorkspaceView';
import { initialConfig } from '../state';

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const builtin: ModelEntry = { name: 'deepseek-chat', displayName: 'deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', apiKeyEnv: 'OPENAI_API_KEY', keyConfigured: true, source: 'builtin' };
const local: ModelEntry = { name: 'qwen2.5-7b', displayName: '内网 Qwen', baseUrl: 'http://127.0.0.1:11434/v1', apiKeyEnv: 'VERISCOPE_QWEN_API_KEY', keyConfigured: false, source: 'local' };
const valid = { name: 'qwen2.5-7b', display_name: '', base_url: 'http://127.0.0.1:11434/v1', api_key_env: 'VERISCOPE_QWEN_API_KEY' };

let requests: ReturnType<typeof vi.fn>;
beforeEach(() => { requests = vi.fn(); vi.stubGlobal('fetch', requests); });

describe('Model registration boundary', () => {
  it('suggests a key variable name from the model name', () => {
    expect(suggestKeyEnv('Qwen2.5-7B')).toBe('VERISCOPE_QWEN2_5_7B_API_KEY');
    expect(suggestKeyEnv('--')).toBe('');
  });

  it.each([
    [{ name: 'qwen2.5:7b' }, '模型名只能含'],
    [{ name: 'deepseek-chat' }, '已存在'],
    [{ base_url: 'ftp://host/v1' }, 'http 或 https'],
    [{ base_url: 'http://user:pw@host/v1' }, '不能包含用户名'],
    [{ base_url: 'http://host/v1?key=1' }, '不能包含用户名'],
    [{ api_key_env: 'OPENAI_API_KEY' }, 'VERISCOPE_'],
  ])('rejects %o before sending', (change, message) => {
    expect(validateLocalModel({ ...valid, ...change }, ['deepseek-chat'])).toMatch(message);
  });

  it('accepts a complete entry and sends no key field', async () => {
    expect(validateLocalModel(valid, ['deepseek-chat'])).toBeNull();
    requests.mockResolvedValue(response({ models: [builtin, local] }));
    await saveLocalModels([valid]);
    const [url, init] = requests.mock.calls[0];
    expect(url).toBe('/api/models/local');
    expect(init.method).toBe('PUT');
    expect(Object.keys(JSON.parse(init.body).models[0]).sort()).toEqual(['api_key_env', 'base_url', 'display_name', 'name']);
  });

  it('maps read-only and malformed responses to readable errors', async () => {
    requests.mockResolvedValueOnce(response({ detail: 'x' }, 423));
    await expect(fetchModels()).rejects.toThrow('公开演示不开放模型配置');
    requests.mockResolvedValueOnce(response({ models: [{ name: 'x' }] }));
    await expect(fetchModels()).rejects.toThrow('结构不完整');
    requests.mockResolvedValueOnce(response({ detail: '模型名 x 已存在。' }, 422));
    await expect(saveLocalModels([valid])).rejects.toThrow('模型名 x 已存在。');
  });
});

describe('Model configuration view', () => {
  it('lists built-in and local models with key status only', async () => {
    requests.mockResolvedValue(response({ models: [builtin, local] }));
    render(<ModelsView/>);
    const builtinTable = await screen.findByRole('table', { name: '内置模型' });
    expect(within(builtinTable).getByText('已设置')).toBeInTheDocument();
    const localTable = screen.getByRole('table', { name: '本机登记的模型' });
    expect(within(localTable).getByText('未设置')).toBeInTheDocument();
    expect(within(localTable).getByText('VERISCOPE_QWEN_API_KEY')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑 deepseek-chat' })).not.toBeInTheDocument();
  });

  it('registers a model, filling the key variable from the name', async () => {
    const changed = vi.fn();
    requests.mockImplementation((url: string, init?: RequestInit) => Promise.resolve(
      init?.method === 'PUT' ? response({ models: [builtin, { ...local, name: 'qwen-max', displayName: 'qwen-max', apiKeyEnv: 'VERISCOPE_QWEN_MAX_API_KEY' }] }) : response({ models: [builtin] })));
    render(<ModelsView onModelsChange={changed}/>);
    await userEvent.click(await screen.findByRole('button', { name: '登记模型' }));
    const form = screen.getByRole('form', { name: '登记模型' });
    await userEvent.type(within(form).getByLabelText('模型名'), 'qwen-max');
    expect(within(form).getByLabelText('密钥变量名')).toHaveValue('VERISCOPE_QWEN_MAX_API_KEY');
    await userEvent.click(within(form).getByRole('button', { name: '保存' }));
    expect(within(form).getByRole('alert')).toHaveTextContent('http 或 https');
    expect(requests.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
    await userEvent.type(within(form).getByLabelText('接口地址'), 'http://10.0.0.5:8000/v1');
    await userEvent.click(within(form).getByRole('button', { name: '保存' }));
    expect(await screen.findByRole('status')).toHaveTextContent('已保存 qwen-max');
    const sent = JSON.parse(requests.mock.calls.find(([, init]) => init?.method === 'PUT')![1].body);
    expect(sent).toEqual({ models: [{ name: 'qwen-max', display_name: '', base_url: 'http://10.0.0.5:8000/v1', api_key_env: 'VERISCOPE_QWEN_MAX_API_KEY' }] });
    await waitFor(() => expect(changed).toHaveBeenLastCalledWith(expect.arrayContaining([expect.objectContaining({ name: 'qwen-max' })])));
  });

  it('shows a notice instead of an error when there is no backend', async () => {
    requests.mockRejectedValue(new TypeError('offline'));
    render(<ModelsView/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地后端');
  });
});

describe('Workspace with registered models', () => {
  it('offers local models after the built-in ones, unselected by default', () => {
    render(<WorkspaceView config={initialConfig} onChange={() => undefined} onRun={() => undefined} running={false} hasLiveResult={false} error={null} configurationCheck={null} localModels={[local]}/>);
    const card = screen.getByRole('button', { name: '选择 内网 Qwen' });
    expect(card).toHaveAttribute('aria-pressed', 'false');
    expect(card).toHaveTextContent('密钥未设置');
    expect(screen.getAllByRole('button', { name: /^选择 / }).at(-1)).toBe(card);
  });
});
