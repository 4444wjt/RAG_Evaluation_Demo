import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { KnowledgeUpdateView } from '../features/KnowledgeUpdateView';
import { labHash, parseLabHash } from '../labRoute';
import type { KUInput, KUMode } from '../api/knowledgeUpdate';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', async (original) => ({ ...await original<typeof import('../config')>(), get READ_ONLY() { return mode.readOnly; }, get STATIC_SITE() { return mode.staticSite; } }));
vi.mock('../components/Charts', () => ({ AbilityChart: () => null, RiskChart: () => null, DurationChart: () => null }));

const source = { source_id: 'nodejs-building', title: 'Node.js BUILDING.md', url: 'https://raw.githubusercontent.com/nodejs/node/main/BUILDING.md', extraction: { fetch_backend: 'html_requests', include_filters: [] }, collector_ready: false };
const versions = [{ timestamp: '1640995200', captured_at: '2021-12-31T23:20:00Z' }, { timestamp: '1640995300', captured_at: '2021-12-31T23:21:40Z' }];
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const bundle = (input: KUInput, runMode: KUMode) => {
  const task = JSON.parse(input.task);
  return { report: { schema_version: 'knowledge-update-report/v2', mode: runMode, run_status: 'COMPLETED', bindings: { entry: 'http', task_fingerprint: 'a'.repeat(64) }, summary: { declared_item_count: task.items.length, result_item_count: task.items.length, unsubmitted_item_count: runMode === 'recheck' ? task.items.length - JSON.parse(input.revisions!).revisions.length : 0, input_error_count: 0 },
    sources: task.sources.map((s: { source_id: string }) => ({ source_id: s.source_id, status: 'CHANGED', diff: '--- old\n+++ new\n-旧原文\n+新原文\n' })),
    items: task.items.map((item: { item_id: string }) => ({ item_id: item.item_id, status: 'PENDING_REVIEW', revision_state: runMode === 'scan' ? 'SCAN_ONLY' : 'NOT_SUBMITTED', semantic_evaluation: 'NOT_EVALUATED', publication_approved: false, new_checks: [{ status: 'AMBIGUOUS', matched_locations: [{ line_start: 1, line_end: 1 }, { line_start: 3, line_end: 3 }] }] })), input_errors: [] }, handover: 'mock handover', run: { schema_version: 'knowledge-update-run/v2', http_status: 200 } };
};
const snapshot = (timestamp: string, text = '旧原文\n连续第二行\n旧原文\n') => ({ source_id: source.source_id, timestamp, captured_at: versions[0].captured_at, sha256: 'b'.repeat(64), size: new TextEncoder().encode(text).length, text });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }

let requests: ReturnType<typeof vi.fn<(url: string, init?: RequestInit) => Promise<Response>>>;
beforeEach(() => {
  mode.readOnly = false; mode.staticSite = false;
  // The step lives in the URL, so a leftover hash from the previous test would start the next one on that step.
  window.history.replaceState({}, '', '#/lab/knowledge-update');
  requests = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/health') return response({ status: 'ok' });
    if (url.endsWith('/sources')) return response({ sources: [source] });
    if (url.endsWith('/versions')) return response({ source_id: source.source_id, versions });
    if (/\/versions\/\d+$/.test(url)) return response(snapshot(url.split('/').pop()!));
    if (url.endsWith('/export')) return new Response(new Blob(['mock zip']), { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="knowledge-update-scan-aaaaaaaaaaaa.zip"' } });
    return response(bundle(JSON.parse(String(init?.body)), url.endsWith('/recheck') ? 'recheck' : 'scan'));
  });
  vi.stubGlobal('fetch', requests);
});
afterEach(() => { mode.readOnly = false; mode.staticSite = false; });

async function selectSource() {
  fireEvent.click(await screen.findByRole('checkbox', { name: /Node.js BUILDING.md/ }));
}
/** The workbench is six pages now; each step is a button whose accessible name is its label. */
const goto = (label: string) => fireEvent.click(screen.getByRole('button', { name: label }));
async function manualSetup() {
  render(<KnowledgeUpdateView/>);
  await selectSource();
  fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前快照'), { target: { value: 'manual' } });
  fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动后快照'), { target: { value: 'manual' } });
  fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前文本'), { target: { value: '  旧原文\n连续第二行\n旧原文\n' } });
  fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动后文本'), { target: { value: '新原文\n新第二行\n' } });
  goto('输入条目');
  fireEvent.change(screen.getByLabelText('原稿'), { target: { value: '旧稿' } });
  const associations = screen.getByRole('group', { name: '关联来源' });
  fireEvent.click(within(associations).getByRole('checkbox', { name: source.title }));
}
async function scan() {
  fireEvent.click(screen.getByRole('button', { name: '扫描 scan' }));
  await screen.findByRole('heading', { name: '扫描结果' });
}
function posted(path: string) { return JSON.parse(String(requests.mock.calls.find(([url]) => String(url).endsWith(`/${path}`))?.[1]?.body)) as KUInput; }

describe('knowledge update routing and deployment modes', () => {
  it('shows an invalid source ID before any form input or scan request', async () => {
    requests.mockResolvedValue(response({ sources: [{ ...source, source_id: 'con', title: '测试来源' }] }));
    render(<KnowledgeUpdateView/>);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('测试来源');
    expect(alert).toHaveTextContent('con');
    expect(alert).toHaveTextContent('保留名称');
    expect(requests).toHaveBeenCalledTimes(1);
    expect(requests).toHaveBeenCalledWith('/api/knowledge-update/sources', expect.objectContaining({ method: 'GET' }));
  });

  it.each([false, true])('supports the knowledge update deep link (demo=%s)', (demo) => {
    const hash = labHash('knowledge-update', demo);
    expect(hash).toBe(demo ? '#/lab/demo/knowledge-update' : '#/lab/knowledge-update');
    expect(parseLabHash(hash)).toEqual({ target: demo ? 'demo' : 'lab', view: 'knowledge-update' });
    expect(parseLabHash(`${hash}/unknown`)).toBeNull();
  });
  it('renders the entry within the preparation navigation', async () => {
    render(<App entryRequest={{ id: 1, target: 'lab', view: 'knowledge-update' }}/>);
    expect(await screen.findByRole('heading', { name: '来源与固定版本' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '知识更新' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('tab', { name: '资料准备' })).toHaveAttribute('aria-selected', 'true');
  });
  it('makes no requests on a static site', () => {
    mode.staticSite = true;
    render(<KnowledgeUpdateView/>);
    expect(screen.getByText(/静态站只提供入口说明/)).toBeInTheDocument();
    expect(requests).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '扫描 scan' })).not.toBeInTheDocument();
  });
  it('probes the independent feature in a READ_ONLY build and explains 404', async () => {
    mode.readOnly = true;
    requests.mockResolvedValue(response({ detail: '知识更新功能未开启。' }, 404));
    render(<KnowledgeUpdateView/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('本站未开启');
    expect(requests).toHaveBeenCalledWith('/api/knowledge-update/sources', expect.objectContaining({ method: 'GET' }));
  });
});

describe('knowledge update editing and results', () => {
  it('generates task paths and exact consecutive old citations from the UI', async () => {
    await manualSetup();
    fireEvent.change(screen.getByLabelText('改动前文本引文来源'), { target: { value: source.source_id } });
    fireEvent.click(screen.getByRole('button', { name: /第 1 行：\s+旧原文/ }));
    fireEvent.click(screen.getByRole('button', { name: '第 2 行：连续第二行' }));
    fireEvent.click(screen.getByRole('button', { name: '添加第 1–2 行引文' }));
    await scan();
    const input = posted('scan'), task = JSON.parse(input.task);
    expect(task.schema_version).toBe('knowledge-update-task/v1');
    expect(task.sources).toEqual([{ source_id: source.source_id, old: { path: `sources/${source.source_id}/old.txt`, version_tag: 'manual' }, new: { path: `sources/${source.source_id}/new.txt`, version_tag: 'manual' } }]);
    expect(input.files[`sources/${source.source_id}/old.txt`]).toBe('  旧原文\n连续第二行\n旧原文\n');
    expect(task.items[0].old_citations).toEqual([{ source_id: source.source_id, quote: '  旧原文\n连续第二行', line_start: 1, line_end: 2 }]);
    expect(input).not.toHaveProperty('revisions');
    expect(screen.getByLabelText(`${source.source_id} unified diff`).textContent).toBe('--- old\n+++ new\n-旧原文\n+新原文\n');
    expect(screen.getByLabelText('item-1 完整逐条结果')).toHaveTextContent('AMBIGUOUS');
    expect(screen.getByText(/MATCHED 只表示字面匹配/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载 ZIP' })).toBeEnabled();
  });
  it('provides manual input after the versions endpoint returns 503', async () => {
    const base = requests.getMockImplementation()!;
    requests.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/sources') ? Promise.resolve(response({ sources: [{ ...source, collector_ready: true }] })) : url.endsWith('/versions') ? Promise.resolve(response({ detail: { code: 'COLLECTOR_UNAVAILABLE' } }, 503)) : base(url, init));
    render(<KnowledgeUpdateView/>); await selectSource();
    expect(await screen.findByRole('alert')).toHaveTextContent('COLLECTOR_UNAVAILABLE');
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前快照'), { target: { value: 'manual' } });
    expect(screen.getByLabelText('Node.js BUILDING.md · 改动前文本')).toBeInTheDocument();
  });
  it('shows full 422 report diagnostics and keeps the draft', async () => {
    await manualSetup();
    requests.mockResolvedValueOnce(response({ report: { run_status: 'INPUT_ERROR', input_errors: [{ code: 'SOURCE_READ_ERROR', location: '$task.sources[0].old', message: 'Source bytes were not supplied.' }] }, handover: 'failed handover', run: { http_status: 422 } }, 422));
    fireEvent.click(screen.getByRole('button', { name: '扫描 scan' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('SOURCE_READ_ERROR'); expect(alert).toHaveTextContent('$task.sources[0].old');
    expect(alert.querySelector('details')).toHaveAttribute('open');
    expect(screen.getByLabelText('原稿')).toHaveValue('旧稿');
    expect(screen.getByRole('button', { name: '下载 ZIP' })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: '扫描结果' })).not.toBeInTheDocument();
  });
  it.each(['原稿', '问题（可选）', '条目 ID', '任务 ID', 'Node.js BUILDING.md · 改动前文本', 'Node.js BUILDING.md · 改动后文本'])('retires current results when editing %s', async (label) => {
    await manualSetup(); await scan();
    goto(label.startsWith('Node.js') ? '选来源与版本' : '输入条目');
    fireEvent.change(screen.getByLabelText(label), { target: { value: 'changed' } });
    expect(screen.getByText(/已过期：/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '扫描结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载 ZIP' })).toBeDisabled();
  });
  it('retires results on revisions and rechecks every item, including unsubmitted ones', async () => {
    await manualSetup(); await scan();
    goto('人工修订');
    fireEvent.click(screen.getByRole('checkbox', { name: '提交本条修订' }));
    expect(screen.getByText(/已过期：/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('修订稿 proposed_draft'), { target: { value: '新稿' } });
    fireEvent.change(screen.getByLabelText('改动后文本引文来源'), { target: { value: source.source_id } });
    fireEvent.click(screen.getByRole('button', { name: '第 1 行：新原文' }));
    fireEvent.click(screen.getByRole('button', { name: '添加第 1–1 行引文' }));
    fireEvent.change(screen.getByLabelText('待确认问题（可选，每行一项）'), { target: { value: '还需人工确认\n' } });
    goto('输入条目');
    fireEvent.click(screen.getByRole('button', { name: '添加条目' }));
    fireEvent.click(screen.getByRole('button', { name: '复核 recheck' }));
    await screen.findByRole('heading', { name: '复核结果' });
    const input = posted('recheck');
    expect(JSON.parse(input.task).items).toHaveLength(2);
    expect(JSON.parse(input.revisions!).revisions).toEqual([{ item_id: 'item-1', proposed_draft: '新稿', citations: [{ source_id: source.source_id, quote: '新原文', line_start: 1, line_end: 1 }], open_questions: ['还需人工确认'] }]);
    expect(screen.getByLabelText('item-2 完整逐条结果')).toHaveTextContent('NOT_SUBMITTED');
    expect(screen.getByLabelText('item-2 完整逐条结果')).toHaveTextContent('"publication_approved": false');
  });
  it.each(['version', 'association', 'source', 'item', 'citation'] as const)('retires results when changing %s', async (change) => {
    await manualSetup();
    if (change === 'citation') fireEvent.change(screen.getByLabelText('改动前文本引文来源'), { target: { value: source.source_id } });
    await scan();
    if (change === 'version' || change === 'source') goto('选来源与版本');
    if (change === 'association' || change === 'item' || change === 'citation') goto('输入条目');
    if (change === 'version') fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前快照'), { target: { value: 'none' } });
    if (change === 'association') fireEvent.click(within(screen.getByRole('group', { name: '关联来源' })).getByRole('checkbox'));
    if (change === 'source') fireEvent.click(screen.getByRole('button', { name: '移除来源' }));
    if (change === 'item') fireEvent.click(screen.getByRole('button', { name: '删除条目' }));
    if (change === 'citation') {
      fireEvent.click(screen.getByRole('button', { name: '第 2 行：连续第二行' }));
      fireEvent.click(screen.getByRole('button', { name: '添加第 2–2 行引文' }));
    }
    expect(screen.getByText(/已过期：/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载 ZIP' })).toBeDisabled();
  });
  it('restores a deleted item together with the citations it owned', async () => {
    await manualSetup();
    fireEvent.change(screen.getByLabelText('改动前文本引文来源'), { target: { value: source.source_id } });
    fireEvent.click(screen.getByRole('button', { name: /^第 1 行：/ }));
    fireEvent.click(screen.getByRole('button', { name: '添加第 1–1 行引文' }));
    fireEvent.click(screen.getByRole('button', { name: '删除条目' }));
    expect(screen.queryByRole('button', { name: '移除引文 1' })).toBeNull();
    // The citations are the part a user cannot rebuild from memory, so undo must bring them back too.
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    expect(screen.getByRole('button', { name: '移除引文 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '删除条目' })).toBeInTheDocument();
    await scan();
    expect(JSON.parse(posted('scan').task).items[0].old_citations).toHaveLength(1);
  });
  it('restores a removed source with its chosen versions instead of dropping the snapshot', async () => {
    await manualSetup();
    goto('选来源与版本');
    fireEvent.click(screen.getByRole('button', { name: '移除来源' }));
    expect(screen.queryByLabelText('Node.js BUILDING.md · 改动前快照')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    expect(screen.getByLabelText('Node.js BUILDING.md · 改动前快照')).toHaveValue('manual');
    expect(screen.getByLabelText('Node.js BUILDING.md · 改动前文本')).toHaveValue('  旧原文\n连续第二行\n旧原文\n');
  });
  it('serializes a selected empty final logical line without losing it', async () => {
    await manualSetup();
    goto('选来源与版本');
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前文本'), { target: { value: 'A\n\nB' } });
    goto('输入条目');
    fireEvent.change(screen.getByLabelText('改动前文本引文来源'), { target: { value: source.source_id } });
    fireEvent.click(screen.getByRole('button', { name: '第 1 行：A' }));
    fireEvent.click(screen.getByRole('button', { name: '第 2 行：空行' }));
    fireEvent.click(screen.getByRole('button', { name: '添加第 1–2 行引文' }));
    await scan();
    expect(JSON.parse(posted('scan').task).items[0].old_citations[0]).toEqual({ source_id: source.source_id, quote: 'A\n\n', line_start: 1, line_end: 2 });
  });
  it('does not treat failed or pending snapshot reads as a deleted source', async () => {
    const base = requests.getMockImplementation()!;
    requests.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/sources') ? Promise.resolve(response({ sources: [{ ...source, collector_ready: true }] })) : /\/versions\/\d+$/.test(url) ? Promise.resolve(response({ code: 'COLLECTOR_ERROR' }, 502)) : base(url, init));
    render(<KnowledgeUpdateView/>); await selectSource();
    await waitFor(() => expect(screen.getByLabelText('Node.js BUILDING.md · 改动前快照').querySelectorAll('option')).toHaveLength(5));
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前快照'), { target: { value: versions[0].timestamp } });
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动后快照'), { target: { value: 'none' } });
    expect(await screen.findByRole('alert')).toHaveTextContent('读取失败不表示来源已删除');
    fireEvent.click(screen.getByRole('button', { name: '扫描 scan' }));
    expect(await screen.findByText(/改动前快照尚未就绪/)).toBeInTheDocument();
    expect(requests.mock.calls.some(([url]) => String(url).endsWith('/scan'))).toBe(false);
  });
});

describe('knowledge update request freshness', () => {
  it.each(['scan', 'recheck'] as const)('ignores a late %s response after edits even if fetch ignores abort', async (runMode) => {
    await manualSetup();
    const late = deferred<Response>();
    requests.mockReturnValueOnce(late.promise);
    fireEvent.click(screen.getByRole('button', { name: runMode === 'scan' ? '扫描 scan' : '复核 recheck' }));
    const input = posted(runMode);
    fireEvent.change(screen.getByLabelText('原稿'), { target: { value: '改过的稿' } });
    await act(async () => late.resolve(response(bundle(input, runMode))));
    expect(screen.queryByRole('heading', { name: runMode === 'scan' ? '扫描结果' : '复核结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载 ZIP' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '扫描 scan' })).toBeEnabled();
  });
  it('ignores an old snapshot after selecting manual text', async () => {
    const late = deferred<Response>(), base = requests.getMockImplementation()!;
    requests.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/sources') ? Promise.resolve(response({ sources: [{ ...source, collector_ready: true }] })) : /\/versions\/\d+$/.test(url) ? late.promise : base(url, init));
    render(<KnowledgeUpdateView/>); await selectSource();
    await waitFor(() => expect(screen.getByLabelText('Node.js BUILDING.md · 改动前快照').querySelectorAll('option')).toHaveLength(5));
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前快照'), { target: { value: versions[0].timestamp } });
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前快照'), { target: { value: 'manual' } });
    fireEvent.change(screen.getByLabelText('Node.js BUILDING.md · 改动前文本'), { target: { value: '手动原文' } });
    await act(async () => late.resolve(response(snapshot(versions[0].timestamp))));
    expect(screen.getByLabelText('Node.js BUILDING.md · 改动前文本')).toHaveValue('手动原文');
    expect(screen.queryByText('正在读取改动前快照…')).not.toBeInTheDocument();
  });
  it('exports exactly the checked input and blocks a late ZIP after editing', async () => {
    await manualSetup(); await scan();
    const createURL = vi.fn(() => 'blob:ku-test');
    const createDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      const late = deferred<Response>(); requests.mockReturnValueOnce(late.promise);
      fireEvent.click(screen.getByRole('button', { name: '下载 ZIP' }));
      expect(posted('export')).toEqual({ ...posted('scan'), mode: 'scan' });
      goto('输入条目');
      fireEvent.change(screen.getByLabelText('原稿'), { target: { value: '导出中改稿' } });
      // A string body keeps the late response constructible in jsdom, where `new Response(blob)` needs Blob.stream().
      await act(async () => late.resolve(new Response('old zip', { headers: { 'Content-Type': 'application/zip' } })));
      expect(createURL).not.toHaveBeenCalled(); expect(click).not.toHaveBeenCalled();
      expect(screen.getByText(/已过期：/)).toBeInTheDocument();
    } finally { if (createDescriptor) Object.defineProperty(URL, 'createObjectURL', createDescriptor); else Reflect.deleteProperty(URL, 'createObjectURL'); }
  });
});
