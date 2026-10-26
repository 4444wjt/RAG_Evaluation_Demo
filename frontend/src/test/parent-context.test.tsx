import { createHash, webcrypto } from 'node:crypto';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { ParentContextView } from '../features/ParentContextView';
import { parseLabHash } from '../labRoute';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', async (importOriginal) => ({
  ...await importOriginal<typeof import('../config')>(),
  get READ_ONLY() { return mode.readOnly; },
  get STATIC_SITE() { return mode.staticSite; },
}));
vi.mock('../components/Charts', () => ({ AbilityChart: () => null, RiskChart: () => null, DurationChart: () => null }));

const labels = {
  source: '完整父条原文 source.txt', segments: '完整段快照 segments.json',
  retrieval: '单次检索命中 retrieval.json', binding: '绑定声明 binding.json',
} as const;
type Key = keyof typeof labels;
const keys = Object.keys(labels) as Key[];
const bytes = {
  source: Buffer.from('\ufeff第一条 原文。\r\n同条第二段。\r\n'),
  segments: Buffer.from('{"data":["原始段快照"]}\r\n'),
  retrieval: Buffer.from('{"resources":["原始命中"]}\n'),
  binding: Buffer.from('{"declaration":"原始绑定"}'),
};
const contextText = '第一条 原文。\n同条第二段。';
const sha = (data: Uint8Array | string) => createHash('sha256').update(data).digest('hex');
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function preparedResponse() {
  return {
    schema: 'ParentContextResponseV1', context_text: contextText,
    report: {
      schema: 'ParentContextPreparationV1', status: 'PREPARED_FOR_REVIEW',
      provider_wire_verified: false, parent_completeness_verified: false, online_index_verified: false,
      human_labels: null, gate_result: null, diagnostics: [], limitations: ['完整性尚未证明。'],
      inputs_sha256: Object.fromEntries(keys.map(key => [key, sha(bytes[key])])),
      source_normalization: { utf8_bom_removed_for_mapping: true, crlf_to_lf_for_mapping: true },
      context: { file: 'context.txt', sha256: sha(contextText), utf8_bytes: Buffer.byteLength(contextText) },
      retrieved_segment_ids: ['segment-2'], expanded_segment_ids: ['segment-1', 'segment-2'],
      expanded_source_positions: [2, 3], source_segment_count: 3, metadata_positions: [],
    },
  };
}
function reviewResponse() {
  const result = preparedResponse();
  return { ...result, context_text: null, report: { ...result.report, status: 'REVIEW', context: null, diagnostics: [{ code: 'UNKNOWN_SEGMENT_ID' }] } };
}

let requests: ReturnType<typeof vi.fn>;
let createURL: ReturnType<typeof vi.fn>;
let revokeURL: ReturnType<typeof vi.fn>;
let downloads: string[];
let originalCreate: PropertyDescriptor | undefined;
let originalRevoke: PropertyDescriptor | undefined;
beforeEach(() => {
  mode.readOnly = false;
  mode.staticSite = false;
  requests = vi.fn().mockResolvedValue(response(preparedResponse()));
  vi.stubGlobal('fetch', requests);
  vi.stubGlobal('crypto', webcrypto);
  originalCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
  originalRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
  createURL = vi.fn().mockReturnValue('blob:parent-context-test');
  revokeURL = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeURL });
  downloads = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download); });
});
afterEach(() => {
  // Every rendered page state in this suite must keep gate verdicts out of the page.
  expect(document.body.textContent).not.toContain('PASS');
  if (originalCreate) Object.defineProperty(URL, 'createObjectURL', originalCreate);
  else Reflect.deleteProperty(URL, 'createObjectURL');
  if (originalRevoke) Object.defineProperty(URL, 'revokeObjectURL', originalRevoke);
  else Reflect.deleteProperty(URL, 'revokeObjectURL');
});

const prepareButton = () => screen.getByRole('button', { name: '准备父条资料' });
const exportButton = () => screen.getByRole('button', { name: '下载复核记录' });
const noVerdict = () => expect(document.body.textContent).not.toContain('PASS');
async function upload(key: Key, content: Uint8Array = bytes[key]) {
  const file = new File([Uint8Array.from(content)], key === 'source' ? 'source.txt' : `${key}.json`);
  fireEvent.change(screen.getByLabelText(labels[key]), { target: { files: [file] } });
  if (content.length) await waitFor(() => expect(screen.getByLabelText(labels[key]).closest('.pc-slot')).toHaveAttribute('data-loaded'));
  noVerdict();
}
async function uploadAll() {
  for (const key of keys) await upload(key);
}
async function prepare(data: unknown = preparedResponse()) {
  requests.mockImplementation(() => Promise.resolve(response(data)));
  render(<ParentContextView/>);
  noVerdict();
  await uploadAll();
  fireEvent.click(prepareButton());
  await screen.findByRole('heading', { name: data && (data as { context_text?: unknown }).context_text !== null ? '资料已准备，等待复核' : '待复核：没有生成资料' });
  noVerdict();
}
function identity(value = '测试复核人') {
  fireEvent.change(screen.getByLabelText('复核人姓名或标识'), { target: { value } });
}
async function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe('Parent context page with mocked local API', () => {
  it('posts all four original byte sequences once, including source BOM and CRLF', async () => {
    await prepare();
    expect(requests).toHaveBeenCalledTimes(1);
    const [url, init] = requests.mock.calls[0];
    expect(url).toBe('/api/contexts/parent/prepare');
    expect(init).toMatchObject({ method: 'POST', headers: { 'Content-Type': 'application/json' } });
    const body = JSON.parse(init.body);
    expect(body.schema).toBe('ParentContextRequestV1');
    expect(body.max_context_bytes).toBe(24000);
    expect(Object.keys(body.files).sort()).toEqual([...keys].sort());
    for (const key of keys) expect(Buffer.from(body.files[key], 'base64')).toEqual(bytes[key]);
    expect(screen.getByText('已在本机复算：四份文件与资料的哈希都和报告一致。')).toBeInTheDocument();
  });

  it('blocks missing files without sending a request', async () => {
    render(<ParentContextView/>);
    expect(prepareButton()).toBeDisabled();
    expect(screen.getByText(/^还需导入：/)).toHaveTextContent('原文 source.txt');
    await upload('source');
    expect(prepareButton()).toBeDisabled();
    expect(screen.getByText(/^还需导入：/)).toHaveTextContent('绑定声明 binding.json');
    fireEvent.click(prepareButton());
    expect(requests).not.toHaveBeenCalled();
  });

  it('rejects an empty file and keeps preparation disabled', async () => {
    render(<ParentContextView/>);
    await upload('source', new Uint8Array());
    for (const key of ['segments', 'retrieval', 'binding'] as Key[]) await upload(key);
    expect(screen.getByText('所选文件为空，未替换。请选择包含内容的文件。')).toBeInTheDocument();
    expect(prepareButton()).toBeDisabled();
    fireEvent.click(prepareButton());
    expect(requests).not.toHaveBeenCalled();
  });

  it('blocks a combined size over 8 MiB even when each file fits', async () => {
    render(<ParentContextView/>);
    await upload('source', new Uint8Array(4 * 1024 * 1024));
    await upload('segments', new Uint8Array(4 * 1024 * 1024));
    await upload('retrieval');
    await upload('binding');
    expect(screen.getByText(/^四份文件合计 .*超过 8 MiB 上限/)).toBeInTheDocument();
    expect(prepareButton()).toBeDisabled();
    fireEvent.click(prepareButton());
    expect(requests).not.toHaveBeenCalled();
  });

  it('aligns preview lines to source positions and downloads exact context only after review', async () => {
    await prepare();
    const lines = within(screen.getByRole('list', { name: '父条资料预览' })).getAllByRole('listitem');
    expect(lines).toHaveLength(2);
    for (const [index, line] of lines.entries()) {
      expect(line.querySelector('.pc-pos')).toHaveTextContent(`原文位置 ${index + 2}`);
      expect(line.querySelector('.pc-line')?.textContent).toBe(contextText.split('\n')[index]);
      expect(line.querySelector('.pc-tag')).toHaveTextContent(index === 1 ? '命中' : '补全');
    }
    const download = screen.getByRole('button', { name: '下载资料 context.txt' });
    expect(download).toBeDisabled();
    identity();
    fireEvent.click(screen.getByRole('radio', { name: '可用于后续处理' }));
    expect(download).toBeDisabled();
    fireEvent.click(screen.getByLabelText('已核对来源、父条范围和预览'));
    expect(download).toBeEnabled();
    fireEvent.click(download);
    expect(downloads).toEqual(['context.txt']);
    expect(await blobText(createURL.mock.calls[0][0])).toBe(contextText);
    await waitFor(() => expect(revokeURL).toHaveBeenCalledWith('blob:parent-context-test'));
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it('keeps REVIEW without context and exports unresolved reasons without labels or gate result', async () => {
    await prepare(reviewResponse());
    expect(screen.queryByRole('button', { name: '下载资料 context.txt' })).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: '父条资料预览' })).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '可用于后续处理' })).toBeDisabled();
    identity();
    fireEvent.click(screen.getByRole('radio', { name: '仍需核查' }));
    expect(exportButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('原因或说明（必填）'), { target: { value: '命中段不在快照中' } });
    expect(exportButton()).toBeEnabled();
    fireEvent.click(exportButton());
    expect(downloads).toEqual(['parent-context-review.json']);
    const record = JSON.parse(await blobText(createURL.mock.calls[0][0]));
    expect(record).toMatchObject({
      schema: 'ParentContextReviewV1', result_status: 'REVIEW', decision: 'needs_further_check',
      note: '命中段不在快照中', gate_result: null, human_labels: null, model_calls: 0, context_sha256: null,
      reviewer: { identity: '测试复核人', independentHumanVerified: false },
      diagnostic_codes: ['UNKNOWN_SEGMENT_ID'],
    });
    expect(requests).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(revokeURL).toHaveBeenCalled());
  });

  it('pauses downloads while a replacement file is reading and clears old results after success', async () => {
    await prepare();
    identity();
    fireEvent.click(screen.getByRole('radio', { name: '可用于后续处理' }));
    fireEvent.click(screen.getByLabelText('已核对来源、父条范围和预览'));
    const actions = [screen.getByRole('button', { name: '下载资料 context.txt' }), exportButton(), screen.getByRole('button', { name: '下载报告 report.json' })];
    for (const action of actions) expect(action).toBeEnabled();
    let resolveRead!: (buffer: ArrayBuffer) => void;
    const file = new File([Uint8Array.from(bytes.retrieval)], 'replacement.json');
    Object.defineProperty(file, 'arrayBuffer', { value: () => new Promise<ArrayBuffer>((resolve) => { resolveRead = resolve; }) });
    fireEvent.change(screen.getByLabelText(labels.retrieval), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByLabelText(labels.retrieval).closest('.pc-slot')).toHaveAttribute('data-state', 'reading'));
    expect(prepareButton()).toBeDisabled();
    for (const action of actions) {
      expect(action).toBeDisabled();
      fireEvent.click(action);
    }
    expect(downloads).toEqual([]);
    expect(requests).toHaveBeenCalledTimes(1);
    await act(async () => { resolveRead(Uint8Array.from(bytes.retrieval).buffer); });
    await waitFor(() => expect(screen.queryByRole('heading', { name: '资料已准备，等待复核' })).not.toBeInTheDocument());
    expect(screen.queryByLabelText('复核人姓名或标识')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载复核记录' })).not.toBeInTheDocument();
    expect(prepareButton()).toBeEnabled();
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it('restores old downloads after a replacement file fails to read without accepting new input', async () => {
    await prepare();
    identity();
    fireEvent.click(screen.getByRole('radio', { name: '可用于后续处理' }));
    fireEvent.click(screen.getByLabelText('已核对来源、父条范围和预览'));
    const actions = [screen.getByRole('button', { name: '下载资料 context.txt' }), exportButton(), screen.getByRole('button', { name: '下载报告 report.json' })];
    let rejectRead!: (error: Error) => void;
    const file = new File(['unreadable replacement'], 'replacement.json');
    Object.defineProperty(file, 'arrayBuffer', { value: () => new Promise<ArrayBuffer>((_resolve, reject) => { rejectRead = reject; }) });
    fireEvent.change(screen.getByLabelText(labels.retrieval), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByLabelText(labels.retrieval).closest('.pc-slot')).toHaveAttribute('data-state', 'reading'));
    expect(prepareButton()).toBeDisabled();
    for (const action of actions) expect(action).toBeDisabled();
    await act(async () => { rejectRead(new Error('controlled read failure')); });
    await screen.findByText('文件读取失败，未替换。请重新选择。');
    expect(screen.getByRole('heading', { name: '资料已准备，等待复核' })).toBeInTheDocument();
    expect(screen.getByLabelText('复核人姓名或标识')).toHaveValue('测试复核人');
    expect(screen.getByLabelText(labels.retrieval).closest('.pc-slot')?.querySelector('.pc-slot-file')).toHaveTextContent(/^retrieval\.json/);
    for (const action of actions) expect(action).toBeEnabled();
    expect(prepareButton()).toBeEnabled();
    fireEvent.click(actions[0]);
    expect(await blobText(createURL.mock.calls[0][0])).toBe(contextText);
    expect(downloads).toEqual(['context.txt']);
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it.each(keys)('clears the prior result and review when %s changes', async (key) => {
    await prepare();
    identity();
    fireEvent.click(screen.getByRole('radio', { name: '可用于后续处理' }));
    fireEvent.click(screen.getByLabelText('已核对来源、父条范围和预览'));
    fireEvent.change(screen.getByLabelText('原因或说明（可选）'), { target: { value: '旧说明' } });
    await upload(key);
    expect(screen.queryByRole('heading', { name: '资料已准备，等待复核' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('复核人姓名或标识')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载复核记录' })).not.toBeInTheDocument();
    fireEvent.click(prepareButton());
    await screen.findByRole('heading', { name: '资料已准备，等待复核' });
    expect(screen.getByLabelText('复核人姓名或标识')).toHaveValue('');
    expect(screen.getByLabelText('原因或说明（可选）')).toHaveValue('');
    expect(screen.getByRole('radio', { name: '可用于后续处理' })).not.toBeChecked();
    expect(screen.getByLabelText('已核对来源、父条范围和预览')).not.toBeChecked();
    expect(exportButton()).toBeDisabled();
  });

  it.each(['readOnly', 'staticSite'] as const)('disables every file input and sends no request in %s mode', (flag) => {
    mode[flag] = true;
    render(<ParentContextView/>);
    expect(screen.getAllByText('公开演示不接收文件，请在本地运行后导入。').length).toBeGreaterThan(0);
    for (const key of keys) {
      const input = screen.getByLabelText(labels[key]);
      expect(input).toBeDisabled();
      fireEvent.change(input, { target: { files: [new File(['blocked'], `${key}.txt`)] } });
    }
    expect(prepareButton()).toBeDisabled();
    expect(document.querySelectorAll('[data-loaded]')).toHaveLength(0);
    fireEvent.click(prepareButton());
    expect(requests).not.toHaveBeenCalled();
  });

  it.each(['detail', 'malformed', 'input-hash', 'context-hash'] as const)('rejects %s errors and retains all four input files', async (kind) => {
    const data = preparedResponse();
    if (kind === 'input-hash') data.report.inputs_sha256.source = '0'.repeat(64);
    if (kind === 'context-hash') data.report.context.sha256 = '0'.repeat(64);
    requests.mockResolvedValue(kind === 'detail' ? response({ detail: '四份文件请求格式错误' }, 422) : response(kind === 'malformed' ? { schema: 'wrong' } : data));
    render(<ParentContextView/>);
    await uploadAll();
    fireEvent.click(prepareButton());
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(kind === 'detail' ? '四份文件请求格式错误' : kind === 'malformed' ? '不完整或不符合约定，未采用' : '哈希不一致，未采用');
    expect(screen.queryByRole('heading', { name: '资料已准备，等待复核' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载复核记录' })).not.toBeInTheDocument();
    for (const key of keys) expect(screen.getByLabelText(labels[key]).closest('.pc-slot')).toHaveAttribute('data-loaded');
    expect(prepareButton()).toBeEnabled();
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it('requires identity and a decision before review can be downloaded', async () => {
    await prepare();
    expect(exportButton()).toBeDisabled();
    identity();
    expect(exportButton()).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: '可用于后续处理' }));
    fireEvent.click(screen.getByLabelText('已核对来源、父条范围和预览'));
    expect(exportButton()).toBeEnabled();
    identity(' ');
    expect(exportButton()).toBeDisabled();
    expect(downloads).toEqual([]);
  });
});

describe('Parent context routes and navigation groups', () => {
  it.each([
    ['#/lab/parent-context', 'lab'], ['#/lab/demo/parent-context', 'demo'],
  ])('parses %s', (hash, target) => {
    expect(parseLabHash(hash)).toEqual({ target, view: 'parent-context' });
  });

  it('selects tabs and opens only their corresponding group', async () => {
    requests.mockResolvedValue(response({ status: 'ok' }));
    render(<App/>);
    await screen.findByText('服务可连接');
    const tabs = within(screen.getByRole('tablist', { name: '导航分组' })).getAllByRole('tab');
    expect(tabs.map(tab => tab.textContent)).toEqual(['评测', '验证与审计', '资料准备']);
    for (const selected of tabs) {
      fireEvent.click(selected);
      for (const tab of tabs) {
        expect(tab).toHaveAttribute('aria-selected', String(tab === selected));
        expect(tab).toHaveAttribute('tabindex', tab === selected ? '0' : '-1');
        const panel = document.getElementById(tab.getAttribute('aria-controls')!)!;
        if (tab === selected) expect(panel).toHaveClass('is-open');
        else expect(panel).not.toHaveClass('is-open');
      }
    }
    fireEvent.click(screen.getByRole('button', { name: /^父条资料准备$/ }));
    expect(screen.getByRole('heading', { level: 1, name: '父条资料准备' })).toBeInTheDocument();
    expect(requests.mock.calls.every(([url]) => url === '/api/health')).toBe(true);
  });

  it('moves tab focus and selection with arrows, Home and End, including wraparound', async () => {
    requests.mockResolvedValue(response({ status: 'ok' }));
    render(<App/>);
    await screen.findByText('服务可连接');
    const tabs = within(screen.getByRole('tablist', { name: '导航分组' })).getAllByRole('tab');
    tabs[0].focus();
    for (const [key, index] of [['ArrowRight', 1], ['ArrowRight', 2], ['ArrowRight', 0], ['ArrowLeft', 2], ['Home', 0], ['End', 2]] as const) {
      fireEvent.keyDown(document.activeElement!, { key });
      expect(tabs[index]).toHaveFocus();
      expect(tabs[index]).toHaveAttribute('aria-selected', 'true');
      expect(document.getElementById(tabs[index].getAttribute('aria-controls')!)).toHaveClass('is-open');
    }
  });
});
