import { createHash, webcrypto } from 'node:crypto';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { CitationVersionView } from '../features/CitationVersionView';
import { AXES, CV_HASH_COMMAND, CV_HASH_NOTE, CV_MAX_BYTES, CV_TIMEOUT_MS, SUBCHECKS, checkCitationVersions, citationReportHtml, inspectCitationFiles, loadCitationFile, suggestedViewUri } from '../api/citationVersion';
import type { CVBundle, CVInput, CVInspection, CVOutput, CVReport, LocalCVFile } from '../api/citationVersion';
import { labHash, parseLabHash } from '../labRoute';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', async (importOriginal) => ({ ...await importOriginal<typeof import('../config')>(), get READ_ONLY() { return mode.readOnly; }, get STATIC_SITE() { return mode.staticSite; } }));
vi.mock('../components/Charts', () => ({ AbilityChart: () => null, RiskChart: () => null, DurationChart: () => null }));

const sha = (bytes: Uint8Array | string) => createHash('sha256').update(bytes).digest('hex');
const DOC = 'http://www.legislation.gov.uk/id/ukpga/2020/1';
const PART = `${DOC}/section/1`;
const VIEW = 'http://www.legislation.gov.uk/ukpga/2020/1/section/1/2020-04-01';
const raw = Buffer.from('\ufeff<?xml version="1.0" encoding="UTF-8"?>\r\n<Legislation>mock fixture bytes</Legislation>\r\n');
const sourceFile = (name = 'source.xml', bytes = raw) => new File([Uint8Array.from(bytes)], name, { type: 'application/xml' });
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const originalFile = (id = 'file-1', bytes = raw): LocalCVFile => ({ id, name: 'source.xml', base64: bytes.toString('base64'), sha256: sha(bytes), size_bytes: bytes.length });
function inspectionFor(body: { files: Array<{ id: string; name: string; base64: string }> }): CVInspection {
  return { schema: 'CitationVersionInspectionV1', files: body.files.map((file) => {
    const bytes = Buffer.from(file.base64, 'base64');
    const invalid = file.name === 'invalid.xml';
    return { id: file.id, name: file.name, sha256: sha(bytes), size_bytes: bytes.length, status: invalid ? 'INVALID_INPUT' : 'READY', error: invalid ? 'XML 语法不合法。' : null,
      observed: invalid ? null : { title: 'Synthetic CLML test source', document_id: DOC, language: 'en', ambiguities: [], provisions: [{ provision_id: PART, view_uri: VIEW, snapshot_date: '2020-04-01', extent: 'E+W', start: '2020-01-01', end: '2021-01-01' }] } };
  }) };
}
function reportFor(input: CVInput): CVReport {
  const rows = input.citations.map((citation) => {
    const subchecks = Object.fromEntries(SUBCHECKS.map((key) => [key, { state: 'MATCH', checks: [{ state: 'MATCH', reason: 'explicit field comparison', evidence: [{ path: `/Legislation/P1/@${key}`, raw: key === 'extent' ? 'E+W' : 'source value' }] }] }])) as CVOutput['subchecks'];
    if (citation.request.extent === 'S') subchecks.extent = { state: 'MISMATCH', checks: [{ state: 'MISMATCH', reason: 'nearest explicit extent membership', evidence: [{ path: '/Legislation/P1/@RestrictExtent', raw: 'E+W' }] }] };
    const output: CVOutput = { method: 'B1', source_sha256: citation.request.source_sha256, subchecks, axes: { snapshot_binding: 'MATCH', fragment_metadata: subchecks.extent.state, known_by: 'REVIEW' }, target_location: { state: 'MATCH', reason: 'explicit field comparison', evidence: [{ path: '/Legislation/P1/@IdURI', raw: PART }] }, owners: { extent: '/Legislation/P1' }, date_roles: { snapshot: { role: 'view', values: [{ path: '/Metadata/valid', raw: '2020-04-01' }] } }, input_errors: [] };
    return { id: citation.id, label: citation.label, file_id: citation.file_id, output };
  });
  return { schema: 'CitationVersionReportV1', checker: { version: 'citation-version-b1-v1', method: 'B1', source_sha256: { rules: sha('rules'), wrapper: sha('wrapper') } }, inputs: { files: input.files.map((file) => ({ id: file.id, name: file.name, sha256: sha(Buffer.from(file.base64, 'base64')), size_bytes: Buffer.from(file.base64, 'base64').length })), citations: structuredClone(input.citations) }, rows,
    counts: { total: rows.length, valid_input: rows.length, invalid: 0, conflicts: rows.filter((row) => row.output.subchecks.extent.state === 'MISMATCH').length, field_review: 0, known_by_review: rows.length }, count_semantics: { overlap: 'conflicts, field_review and known_by_review overlap' }, limitations: ['仅为字段核对。'] };
}
function sampleInput(files = [originalFile()]): CVInput {
  return { schema: 'CitationVersionCheckV1', files: files.map(({ id, name, base64 }) => ({ id, name, base64 })), citations: [{ id: 'citation-1', label: '', file_id: files[0].id, request: { source_sha256: files[0].sha256, document_id: DOC, provision_id: PART, view_uri: VIEW, snapshot_date: '2020-04-01', check_date: '2020-04-01', extent: 'E', language: 'en' } }] };
}

function largeReviewReport(report: CVReport): CVReport {
  report.rows[0].output.subchecks.conditions = { state: 'REVIEW', checks: Array.from({ length: 66 }, (_, index) => ({ state: 'REVIEW', reason: 'document-level UnappliedEffect not excluded under this contract', evidence: [
    { path: `/Metadata/UnappliedEffect[${index + 1}]`, raw: `BEGIN-${index}\n${'Complete unapplied effect source content.\n'.repeat(120)}END-${index}` },
    { path: `/Metadata/UnappliedEffect[${index + 1}]/@Id`, raw: `effect-${index}` },
  ] })) };
  report.rows[0].output.axes.fragment_metadata = report.rows[0].output.subchecks.extent.state === 'MISMATCH' ? 'MISMATCH' : 'REVIEW';
  report.counts.field_review = 1;
  return report;
}

let requests: ReturnType<typeof vi.fn>;
let createURL: ReturnType<typeof vi.fn>;
let revokeURL: ReturnType<typeof vi.fn>;
let downloads: string[];
const originalCreateURL = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
const originalRevokeURL = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
beforeEach(() => {
  mode.readOnly = false; mode.staticSite = false;
  vi.stubGlobal('crypto', webcrypto);
  requests = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/health') return response({ status: 'ok' });
    const body = JSON.parse(String(init?.body));
    return response(url.endsWith('/inspect') ? inspectionFor(body) : reportFor(body));
  });
  vi.stubGlobal('fetch', requests);
  createURL = vi.fn(() => 'blob:cv-report'); revokeURL = vi.fn(); downloads = [];
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeURL });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download); });
});
afterEach(() => {
  vi.useRealTimers();
  if (originalCreateURL) Object.defineProperty(URL, 'createObjectURL', originalCreateURL); else Reflect.deleteProperty(URL, 'createObjectURL');
  if (originalRevokeURL) Object.defineProperty(URL, 'revokeObjectURL', originalRevokeURL); else Reflect.deleteProperty(URL, 'revokeObjectURL');
});
const group = (index = 1) => within(screen.getByRole('group', { name: `引用条目 ${index}` }));
const checkButton = () => screen.getByRole('button', { name: '检查版本字段' });
async function upload(files = [sourceFile()]) {
  fireEvent.change(screen.getByLabelText('添加 CLML XML 文件'), { target: { files } });
  await screen.findByText('原件观察已就绪。请明确选择条款，并填写你的版本需求。');
}
function fill(index = 1, fileId = 'file-1', extent = 'E') {
  const ui = group(index);
  fireEvent.change(ui.getByLabelText('引用对应的文件'), { target: { value: fileId } });
  fireEvent.change(ui.getByLabelText('引用的条款'), { target: { value: '0' } });
  fireEvent.change(ui.getByLabelText('所需视图日期'), { target: { value: '2020-04-01' } });
  fireEvent.click(ui.getByLabelText('与所需视图日期相同'));
  fireEvent.change(ui.getByLabelText('所需地域'), { target: { value: extent } });
  fireEvent.change(ui.getByLabelText('所需语言'), { target: { value: 'en' } });
  fireEvent.click(ui.getByRole('button', { name: '按此日期准备核对' }));
}
async function run() { fireEvent.click(checkButton()); await screen.findByRole('heading', { name: '本次字段核对' }); }
async function completed(extent = 'E') { render(<CitationVersionView/>); await upload(); fill(1, 'file-1', extent); await run(); }
const blobText = (blob: Blob): Promise<string> => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(blob); });

describe('citation version workflow', () => {
  it('keeps the first screen actionable and reveals the requirement form only after inspection', async () => {
    render(<CitationVersionView/>);
    expect(screen.queryByRole('group', { name: '引用条目 1' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '检查版本字段' })).not.toBeInTheDocument();
    expect(screen.getByText('从原件开始，按需求核对')).toBeVisible();
    const steps = within(screen.getByRole('list', { name: '操作流程' })).getAllByRole('listitem');
    expect(steps[0]).toHaveAttribute('aria-current', 'step');
    expect(steps[1]).not.toHaveAttribute('aria-current');
    await upload();
    expect(screen.getByRole('group', { name: '引用条目 1' })).toBeVisible();
    expect(steps[1]).toHaveAttribute('aria-current', 'step');
    expect(group().getByLabelText('所需视图日期')).toHaveValue('');
  });

  it('advances the workflow from checking only when a real reply arrives', async () => {
    render(<CitationVersionView/>); await upload(); fill();
    let resolveCheck!: (value: Response) => void;
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveCheck = resolve; }));
    fireEvent.click(checkButton());
    const steps = within(screen.getByRole('list', { name: '操作流程' })).getAllByRole('listitem');
    expect(steps[2]).toHaveAttribute('aria-current', 'step');
    expect(steps[3]).not.toHaveAttribute('aria-current');
    expect(screen.getByText('等待本次结果')).toBeVisible();
    const input = JSON.parse(requests.mock.calls[1][1].body);
    await act(async () => resolveCheck(response(reportFor(input))));
    await screen.findByRole('heading', { name: '本次字段核对' });
    expect(steps[3]).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('可下载复核包')).toBeVisible();
    expect(screen.getByText('当时已知信息（known_by）')).toBeVisible();
  });

  it('keeps observed date, region, language and selected identities out of blank expectations', async () => {
    render(<CitationVersionView/>); await upload();
    const ui = group();
    for (const label of ['引用对应的文件', '引用的条款', '所需视图日期', '片段查询日期', '所需地域', '所需语言', '所需视图 URI（请求）']) expect(ui.getByLabelText(label)).toHaveValue('');
    expect(ui.getByLabelText('与所需视图日期相同')).not.toBeChecked();
    expect(screen.getByText('E+W')).toBeInTheDocument();
    fireEvent.change(ui.getByLabelText('引用对应的文件'), { target: { value: 'file-1' } });
    fireEvent.change(ui.getByLabelText('引用的条款'), { target: { value: '0' } });
    expect(ui.getByLabelText('所需视图日期')).toHaveValue('');
    expect(ui.getByLabelText('所需视图 URI（请求）')).toHaveValue('');
    fireEvent.click(checkButton());
    expect(await screen.findByText('请填写所需视图日期；原件日期不会自动作为你的要求。')).toBeInTheDocument();
    expect(ui.getByLabelText('所需视图日期')).toHaveFocus();
    expect(requests).toHaveBeenCalledTimes(1);
  });

  it('posts original BOM/CRLF bytes with explicitly selected identity and independent requirements', async () => {
    await completed('S');
    expect(requests).toHaveBeenCalledTimes(2);
    const submitted = JSON.parse(requests.mock.calls[1][1].body) as CVInput;
    expect(requests.mock.calls[1][0]).toBe('/api/citations/versions/check');
    expect(Buffer.from(submitted.files[0].base64, 'base64')).toEqual(raw);
    expect(submitted.citations[0].request).toEqual({ source_sha256: sha(raw), document_id: DOC, provision_id: PART, view_uri: VIEW, snapshot_date: '2020-04-01', check_date: '2020-04-01', extent: 'S', language: 'en' });
    const result = screen.getByRole('article');
    expect(within(result).getByText('/Legislation/P1/@RestrictExtent')).toBeInTheDocument();
    expect(within(result).getByText('E+W')).toBeInTheDocument();
    expect(within(result).getByText('查看地域字段；确认所需地域，或查找覆盖该地域的资料。')).toBeInTheDocument();
    expect(within(result).getByText('当时已知信息（known_by）')).toBeVisible();
    expect(result.textContent).not.toContain('PASS');
    expect(result.querySelectorAll('.cv-check')).toHaveLength(7);
  });

  it('keeps 66 long REVIEW records collapsed with visible status, reason, counts and next action, without losing evidence', async () => {
    render(<CitationVersionView/>); await upload(); fill(1, 'file-1', 'S');
    let expectedReport!: CVReport;
    requests.mockImplementationOnce(async (_url: string, init: RequestInit) => {
      expectedReport = largeReviewReport(reportFor(JSON.parse(String(init.body))));
      return response(expectedReport);
    });
    await run();
    const article = screen.getByRole('article');
    const condition = article.querySelector<HTMLElement>('[data-check="conditions"]')!;
    const details = condition.querySelector<HTMLDetailsElement>('details')!;
    const region = condition.querySelector<HTMLElement>('.cv-evidence-scroll')!;
    expect(details).not.toHaveAttribute('open');
    expect(within(condition).getByText('REVIEW')).toBeVisible();
    expect(within(condition).getByText('66 条检查 · 132 项原字段记录')).toBeVisible();
    expect(condition.querySelector('.cv-check-reason')).toBeVisible();
    expect(condition.querySelector('.cv-check-reason')).toHaveTextContent('document-level UnappliedEffect');
    expect(condition.querySelector('.cv-next')).toBeVisible();
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region).toHaveAttribute('role', 'region');
    expect(region).toHaveAccessibleName('引用条目 1 附加条件完整依据');
    expect(region).not.toBeVisible();
    expect(region.querySelectorAll('.cv-evidence > div')).toHaveLength(132);
    expect(region.textContent).toContain('BEGIN-0');
    expect(region.textContent).toContain('END-65');
    expect(article.querySelector('[data-check="extent"] details')).toHaveAttribute('open');
    fireEvent.click(within(condition).getByText('查看完整原字段与逐项原因'));
    expect(details).toHaveAttribute('open');
    expect(region).toBeVisible();
    region.focus(); expect(region).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: '下载本次 JSON 复核包' }));
    const bundle = JSON.parse(await blobText(createURL.mock.calls[0][0])) as CVBundle;
    expect(bundle.report).toEqual(expectedReport);
  });

  it('binds same-named files by unique IDs and hashes, never by filename', async () => {
    render(<CitationVersionView/>); const second = Buffer.from('different source bytes');
    await upload([sourceFile('same.xml'), sourceFile('same.xml', second)]);
    const inspected = JSON.parse(requests.mock.calls[0][1].body);
    expect(inspected.files.map((file: { id: string }) => file.id)).toEqual(['file-1', 'file-2']);
    fill(1, 'file-2'); await run();
    const submitted = JSON.parse(requests.mock.calls[1][1].body);
    expect(submitted.citations[0].file_id).toBe('file-2');
    expect(submitted.citations[0].request.source_sha256).toBe(sha(second));
    expect(submitted.files).toHaveLength(2);
  });

  it('keeps invalid-file observations visible alongside usable files', async () => {
    render(<CitationVersionView/>); await upload([sourceFile(), sourceFile('invalid.xml', Buffer.from('<bad'))]);
    expect(screen.getByText('XML 语法不合法。')).toBeVisible();
    expect(screen.getByText(/^2 \/ 10 份/)).toBeInTheDocument();
    fill(); await run();
    const submitted = JSON.parse(requests.mock.calls[1][1].body);
    expect(submitted.files).toHaveLength(2);
    expect(submitted.citations).toHaveLength(1);
  });

  it('retires downloads immediately on edits and preserves the exact previous input separately', async () => {
    await completed('S');
    fireEvent.click(screen.getByRole('button', { name: '下载本次 JSON 复核包' }));
    const oldText = await blobText(createURL.mock.calls[0][0]);
    const oldBundle = JSON.parse(oldText) as CVBundle;
    fireEvent.change(group().getByLabelText('所需地域'), { target: { value: 'E' } });
    expect(screen.queryByRole('heading', { name: '本次字段核对' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载本次 JSON 复核包' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下载上次 JSON' }));
    expect(await blobText(createURL.mock.calls[1][0])).toBe(oldText);
    expect(downloads[1]).toBe('citation-version-previous-bundle.json');
    await run();
    fireEvent.click(screen.getByRole('button', { name: '下载本次 JSON 复核包' }));
    const newBundle = JSON.parse(await blobText(createURL.mock.calls[2][0])) as CVBundle;
    expect(oldBundle.input.citations[0].request.extent).toBe('S');
    expect(newBundle.input.citations[0].request.extent).toBe('E');
    expect(newBundle.input.files).toEqual(oldBundle.input.files);
    expect(oldBundle.report.counts.conflicts).toBe(1); expect(newBundle.report.counts.conflicts).toBe(0);
  });

  it('retains all other request bindings when one of two citations is corrected', async () => {
    render(<CitationVersionView/>); await upload(); fill(1, 'file-1', 'S');
    fireEvent.click(screen.getByRole('button', { name: '添加引用条目' })); fill(2); await run();
    const oldInput = JSON.parse(requests.mock.calls[1][1].body);
    fireEvent.change(group(1).getByLabelText('所需地域'), { target: { value: 'E' } }); await run();
    const newInput = JSON.parse(requests.mock.calls[2][1].body);
    expect(newInput.citations[1]).toEqual(oldInput.citations[1]);
    expect(newInput.files).toEqual(oldInput.files);
    expect(screen.getAllByRole('article')).toHaveLength(2);
  });

  it('withdraws current exports when a file is added or a selected file is removed', async () => {
    await completed();
    await upload([sourceFile('another.xml', Buffer.from('another'))]);
    expect(screen.queryByRole('button', { name: '下载本次 HTML 报告' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '移除文件 1 source.xml' }));
    expect(group().getByLabelText('引用对应的文件')).toHaveValue('');
    expect(group().getByLabelText('引用的条款')).toHaveValue('');
  });

  it('requires an explicit URI preparation action and clears the generated URI on date change', async () => {
    render(<CitationVersionView/>); await upload(); fill();
    fireEvent.change(group().getByLabelText('所需视图日期'), { target: { value: '2019-01-01' } });
    expect(group().getByLabelText('所需视图 URI（请求）')).toHaveValue('');
    fireEvent.click(checkButton());
    expect(await screen.findByText('请点击“按此日期准备核对”，或手动填写所需视图 URI。')).toBeVisible();
    expect(group().getByLabelText('所需视图 URI（请求）')).toHaveFocus();
    fireEvent.click(group().getByRole('button', { name: '按此日期准备核对' }));
    expect(group().getByLabelText('所需视图 URI（请求）')).toHaveValue(VIEW.replace('2020-04-01', '2019-01-01'));
    expect(group().getByLabelText('片段查询日期')).toHaveValue('2019-01-01');
  });

  it('recovers from a service failure without dropping requirements or uploading again', async () => {
    render(<CitationVersionView/>); await upload(); fill();
    requests.mockRejectedValueOnce(new TypeError('offline'));
    fireEvent.click(checkButton());
    expect(await screen.findByText(/无法取得完整结果/)).toBeVisible();
    expect(group().getByLabelText('所需地域')).toHaveValue('E');
    expect(screen.queryByRole('button', { name: '下载本次 JSON 复核包' })).not.toBeInTheDocument();
    await run();
    expect(requests.mock.calls.filter(([url]) => url.endsWith('/inspect'))).toHaveLength(1);
  });

  it.each([
    [409, '检查器源码在处理期间发生变化，请确认版本后重新检查。'],
    [413, 'XML 资源总量超过安全限制，请减少文件后重新检查。'],
  ])('shows a readable HTTP %s rejection and retains the entered requirements', async (status, message) => {
    render(<CitationVersionView/>); await upload(); fill();
    requests.mockResolvedValueOnce(response({ detail: message }, Number(status)));
    fireEvent.click(checkButton());
    expect(await screen.findByText(String(message))).toBeVisible();
    expect(group().getByLabelText('所需视图日期')).toHaveValue('2020-04-01');
    expect(group().getByLabelText('所需地域')).toHaveValue('E');
    expect(screen.queryByRole('button', { name: '下载本次 JSON 复核包' })).not.toBeInTheDocument();
    expect(checkButton()).toBeEnabled();
  });

  it('ignores a late check reply after cancellation and after a requirement edit', async () => {
    render(<CitationVersionView/>); await upload(); fill();
    let resolveCheck!: (value: Response) => void;
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveCheck = resolve; }));
    fireEvent.click(checkButton());
    const input = JSON.parse(requests.mock.calls[1][1].body);
    expect(screen.getByRole('button', { name: '正在检查…' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '取消本次操作' }));
    await act(async () => resolveCheck(response(reportFor(input))));
    expect(screen.queryByRole('heading', { name: '本次字段核对' })).not.toBeInTheDocument();
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveCheck = resolve; }));
    fireEvent.click(checkButton());
    fireEvent.change(group().getByLabelText('所需地域'), { target: { value: 'S' } });
    expect(requests.mock.calls[2][1].signal.aborted).toBe(true);
    await act(async () => resolveCheck(response(reportFor(input))));
    expect(screen.queryByRole('button', { name: '下载本次 HTML 报告' })).not.toBeInTheDocument();
  });

  it('aborts pending work on unmount', async () => {
    const view = render(<CitationVersionView/>); await upload(); fill();
    let resolveCheck!: (value: Response) => void;
    requests.mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveCheck = resolve; }));
    fireEvent.click(checkButton());
    const input = JSON.parse(requests.mock.calls[1][1].body);
    view.unmount();
    expect(requests.mock.calls[1][1].signal.aborted).toBe(true);
    await act(async () => resolveCheck(response(reportFor(input))));
    expect(createURL).not.toHaveBeenCalled();
  });

  it.each(['count', 'size', 'extension'])('rejects invalid upload %s before reading or sending', async (caseName) => {
    render(<CitationVersionView/>);
    const file = sourceFile(caseName === 'extension' ? 'notes.txt' : 'source.xml');
    if (caseName === 'size') Object.defineProperty(file, 'size', { value: CV_MAX_BYTES + 1 });
    const reads = vi.spyOn(FileReader.prototype, 'readAsArrayBuffer');
    fireEvent.change(screen.getByLabelText('添加 CLML XML 文件'), { target: { files: caseName === 'count' ? Array.from({ length: 11 }, () => file) : [file] } });
    expect(screen.getByRole('alert')).toBeVisible(); expect(reads).not.toHaveBeenCalled(); expect(requests).not.toHaveBeenCalled();
  });

  it.each(['readOnly', 'staticSite'] as const)('blocks input reads, drops and API calls in %s builds', async (key) => {
    mode[key] = true;
    render(<CitationVersionView/>);
    const reads = vi.spyOn(FileReader.prototype, 'readAsArrayBuffer');
    const input = screen.getByLabelText('添加 CLML XML 文件');
    expect(input).toBeDisabled();
    fireEvent.change(input, { target: { files: [sourceFile()] } });
    fireEvent.drop(screen.getByText('选择或拖入 XML 文件'), { dataTransfer: { files: [sourceFile()] } });
    await expect(loadCitationFile(sourceFile(), 'file-1')).rejects.toMatchObject({ code: 'READ_ONLY' });
    await expect(inspectCitationFiles([originalFile()])).rejects.toMatchObject({ code: 'READ_ONLY' });
    expect(reads).not.toHaveBeenCalled(); expect(requests).not.toHaveBeenCalled();
  });

  it('downloads repeatable self-contained bundles and fixed report filenames', async () => {
    await completed();
    expect(screen.getByText(CV_HASH_NOTE)).toBeInTheDocument();
    expect(screen.getByText(CV_HASH_COMMAND)).toBeInTheDocument();
    const button = screen.getByRole('button', { name: '下载本次 JSON 复核包' });
    fireEvent.click(button); fireEvent.click(button);
    const first = await blobText(createURL.mock.calls[0][0]);
    expect(await blobText(createURL.mock.calls[1][0])).toBe(first);
    const bundle = JSON.parse(first) as CVBundle;
    expect(bundle.schema).toBe('CitationVersionBundleV1');
    expect(Buffer.from(bundle.input.files[0].base64, 'base64')).toEqual(raw);
    expect(bundle.input.citations).toEqual(bundle.report.inputs.citations);
    expect(bundle.report.inputs.files[0].sha256).toBe(sha(raw));
    fireEvent.click(screen.getByRole('button', { name: '下载本次 HTML 报告' }));
    const html = await blobText(createURL.mock.calls[2][0]);
    expect(html).toContain(CV_HASH_NOTE);
    expect(html).toContain(CV_HASH_COMMAND);
    expect(bundle.report.limitations).toEqual(['仅为字段核对。']);
    expect(downloads).toEqual(['citation-version-bundle.json', 'citation-version-bundle.json', 'citation-version-report.html']);
    await waitFor(() => expect(revokeURL).toHaveBeenCalledTimes(3));
  });
});

describe('citation version response and export boundaries', () => {
  it.each(['source-hash', 'request', 'missing-row', 'known-by', 'counts', 'missing-evidence'])('rejects an altered %s response', async (change) => {
    const files = [originalFile()]; const input = sampleInput(files); const report = reportFor(input);
    if (change === 'source-hash') report.inputs.files[0].sha256 = sha('wrong');
    if (change === 'request') report.inputs.citations[0].request.extent = 'S';
    if (change === 'missing-row') report.rows = [];
    if (change === 'known-by') report.rows[0].output.axes.known_by = 'MATCH';
    if (change === 'counts') report.counts.conflicts = 1;
    if (change === 'missing-evidence') Reflect.deleteProperty(report.rows[0].output.subchecks.extent.checks[0], 'evidence');
    requests.mockResolvedValueOnce(response(report));
    await expect(checkCitationVersions(input, files)).rejects.toMatchObject({ code: change === 'counts' ? 'COUNTS' : 'RESPONSE' });
  });

  it('rejects a mismatched inspection before it can supply a source binding', async () => {
    const files = [originalFile()]; const observed = inspectionFor({ files }); observed.files[0].sha256 = sha('another');
    requests.mockResolvedValueOnce(response(observed));
    await expect(inspectCitationFiles(files)).rejects.toMatchObject({ code: 'RESPONSE' });
  });

  it('accepts semantic JSON equality without depending on request object key order', async () => {
    const files = [originalFile()]; const input = sampleInput(files); const report = reportFor(input);
    report.inputs.citations[0].request = Object.fromEntries(Object.entries(report.inputs.citations[0].request).reverse()) as CVInput['citations'][number]['request'];
    requests.mockResolvedValueOnce(response(report));
    expect((await checkCitationVersions(input, files)).input).toEqual(input);
  });

  it('keeps invalid rows and missing fields distinct from zero or a successful check', async () => {
    const files = [originalFile()]; const input = sampleInput(files); const report = reportFor(input);
    const output = report.rows[0].output;
    output.axes = Object.fromEntries(AXES.map((key) => [key, 'INVALID_INPUT'])) as CVOutput['axes'];
    for (const key of SUBCHECKS) output.subchecks[key] = { state: 'INVALID_INPUT', checks: [] };
    output.input_errors = ['request URI is invalid']; output.target_location = null;
    report.counts = { total: 1, valid_input: 0, invalid: 1, conflicts: 0, field_review: 0, known_by_review: 0 };
    requests.mockResolvedValueOnce(response(report));
    const bundle = await checkCitationVersions(input, files);
    expect(bundle.report.rows[0].output.axes.known_by).toBe('INVALID_INPUT');
    expect(bundle.report.rows[0].output.target_location).toBeNull();
  });

  it('escapes every untrusted HTML field, embeds no resources or executable script, and labels history', () => {
    const input = sampleInput(); const report = reportFor(input); const attack = '<script>alert("x")</script><img src="https://remote.invalid/x">';
    input.citations[0].label = attack; report.rows[0].label = attack; report.inputs.files[0].name = attack;
    report.rows[0].output.subchecks.extent.checks[0].evidence[0] = { path: attack, raw: attack };
    const html = citationReportHtml({ schema: 'CitationVersionBundleV1', input, report }, true);
    const document = new DOMParser().parseFromString(html, 'text/html');
    expect(document.querySelectorAll('script,img,iframe,link,object,[src],[href]')).toHaveLength(0);
    expect(document.body.textContent).toContain(attack);
    expect(document.body.textContent).toContain('历史报告，仅对应本包保存的原始文件与请求');
    expect(document.querySelector('meta[http-equiv="Content-Security-Policy"]')).not.toBeNull();
  });

  it('exports compact HTML overviews and complete expandable evidence plus byte-equivalent JSON values', () => {
    const input = sampleInput(); input.citations[0].request.extent = 'S';
    const report = largeReviewReport(reportFor(input));
    const bundle: CVBundle = { schema: 'CitationVersionBundleV1', input, report };
    const before = JSON.stringify(bundle);
    const document = new DOMParser().parseFromString(citationReportHtml(bundle), 'text/html');
    const condition = document.querySelector('[data-check="conditions"]')!;
    expect(condition.getAttribute('data-state')).toBe('REVIEW');
    expect(condition.querySelector('details')!.hasAttribute('open')).toBe(false);
    expect(condition.querySelector(':scope > .reason')?.textContent).toContain('document-level UnappliedEffect');
    expect(Array.from(condition.children).filter((node) => node.tagName === 'P').map((node) => node.textContent).join('\n')).toContain('66 条检查 · 132 项原字段记录');
    expect(Array.from(condition.children).filter((node) => node.tagName === 'P').map((node) => node.textContent).join('\n')).toContain('下一步：');
    const region = condition.querySelector('.evidence-scroll')!;
    expect(region.getAttribute('tabindex')).toBe('0');
    expect(region.getAttribute('role')).toBe('region');
    expect(region.querySelectorAll('.check-record')).toHaveLength(66);
    expect(region.querySelectorAll('dt')).toHaveLength(132);
    expect(region.textContent).toContain('END-65');
    expect(document.querySelector('[data-check="extent"] details')!.hasAttribute('open')).toBe(true);
    expect(JSON.parse(document.querySelector('[data-raw-output="0"]')!.textContent!)).toEqual(report.rows[0].output);
    expect(JSON.stringify(bundle)).toBe(before);
    expect(document.querySelectorAll('script,img,iframe,link,object,[src],[href]')).toHaveLength(0);
  });

  it('times out once without retrying and distinguishes cancellation from a timeout', async () => {
    vi.useFakeTimers();
    requests.mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))));
    const pending = expect(inspectCitationFiles([originalFile()])).rejects.toMatchObject({ code: 'TIMEOUT' });
    await vi.advanceTimersByTimeAsync(CV_TIMEOUT_MS); await pending;
    expect(requests).toHaveBeenCalledTimes(1);
    const controller = new AbortController();
    const cancelled = expect(inspectCitationFiles([originalFile()], controller.signal)).rejects.toMatchObject({ code: 'CANCELLED' });
    controller.abort(); await cancelled;
    expect(requests).toHaveBeenCalledTimes(2);
  });

  it('never substitutes a source date for an absent desired date or guesses an unfamiliar URI', () => {
    expect(suggestedViewUri(VIEW, '')).toBeNull();
    expect(suggestedViewUri('http://other.invalid/2020-04-01', '2019-01-01')).toBeNull();
    expect(suggestedViewUri(`${VIEW}/data.xml`, '2019-01-01')).toBeNull();
    expect(suggestedViewUri(VIEW, '2019-01-01')).toBe(VIEW.replace('2020-04-01', '2019-01-01'));
  });
});

describe('citation version navigation', () => {
  it('keeps all navigation groups reachable by keyboard while preserving the current task marker', async () => {
    render(<App entryRequest={{ id: 2, target: 'lab', view: 'citation-version' }}/>);
    await screen.findByRole('heading', { name: '引用版本检查' });
    const tabs = screen.getByRole('tablist', { name: '导航分组' });
    fireEvent.keyDown(tabs, { key: 'Home' });
    expect(screen.getByRole('tab', { name: '评测' })).toHaveFocus();
    expect(document.getElementById('nav-group-eval-panel')).toHaveClass('is-open');
    expect(document.getElementById('nav-group-prep-panel')).not.toHaveClass('is-open');
    expect(screen.getByRole('button', { name: '引用版本检查' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('tab', { name: '资料准备' }).querySelector('.nav-tab-mark')).not.toBeNull();
    fireEvent.keyDown(tabs, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: '验证与审计' })).toHaveFocus();
    expect(document.getElementById('nav-group-audit-panel')).toHaveClass('is-open');
    fireEvent.keyDown(tabs, { key: 'End' });
    expect(screen.getByRole('tab', { name: '资料准备' })).toHaveFocus();
    expect(document.getElementById('nav-group-prep-panel')).toHaveClass('is-open');
    expect(screen.getByRole('heading', { name: '引用版本检查' })).toBeVisible();
    expect(screen.getByLabelText('添加 CLML XML 文件')).toBeEnabled();
  });

  it.each([false, true])('supports the deep link and history hash with demo=%s', (demo) => {
    const hash = labHash('citation-version', demo);
    expect(parseLabHash(hash)).toEqual({ target: demo ? 'demo' : 'lab', view: 'citation-version' });
  });
  it.each([false, true])('also parses the knowledge update view with demo=%s', (demo) => {
    expect(parseLabHash(labHash('knowledge-update', demo))).toEqual({ target: demo ? 'demo' : 'lab', view: 'knowledge-update' });
  });
  it('opens from a fresh deep-link entry in the preparation navigation group', async () => {
    render(<App entryRequest={{ id: 1, target: 'lab', view: 'citation-version' }}/>);
    expect(await screen.findByRole('heading', { name: '引用版本检查' })).toBeVisible();
    expect(screen.getByRole('tab', { name: '资料准备' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: '引用版本检查' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: '加载演示' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建评测' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '父条资料准备' }));
    expect(screen.getByRole('button', { name: '加载演示' })).toBeVisible();
    expect(screen.getByRole('button', { name: '新建评测' })).toBeVisible();
  });
});
