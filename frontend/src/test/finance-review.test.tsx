import { act, fireEvent, render, screen } from '@testing-library/react';
import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FinanceReviewView } from '../features/FinanceReviewView';
import { checkFinanceReview, readFinanceFile } from '../api/financeReview';
import { parseLabHash } from '../labRoute';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', () => ({ get READ_ONLY() { return mode.readOnly; }, get STATIC_SITE() { return mode.staticSite; }, BYOK: false }));
const sha = (text: string) => createHash('sha256').update(text).digest('hex');
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const source = '第一条　资料窗口应当在7个工作日内答复申请。';
const input = { schemaVersion: 1 as const, sourceFormat: 'article-lines-v1' as const, source, answer: '期限为14个工作日。', contexts: [source], engine: 'base' as const, question: '' };
function reportFor(raw: string, status = 'no_error_found') {
  const flagged = status === 'findings' ? true : status === 'no_error_found' ? false : null;
  const engine = JSON.parse(raw).engine;
  const value = JSON.parse(raw).answer.includes('0.1%') ? '0.1%' : '14个工作日';
  const findings = flagged ? [{ code: 'QUANTITY_UNSUPPORTED', severity: 'error', title: '数额或期限在资料与题目中都找不到', text: value, detail: '资料与题目中都没有这一数额' }] : [];
  const original = { flagged, findings, contextProvisions: [['第十九条第二款']] };
  return { schemaVersion: 1, status, flagged, engine, diagnostics: flagged === null ? [{ code: 'NO_PROVISIONS', message: '没有可识别的条号。' }] : [],
    sourceAudit: { nonblankLines: 1, parsedLines: 1, unparsedLines: [], articleCount: 1 },
    result: flagged === null ? null : engine === 'base' ? original : { original, extensionFindings: [], recoveries: [], flagged },
    structureCheck: { status: 'not_provided', referenceSha256: null, expectedUnits: null, observedUnits: 1, scope: 'all_paragraph_item_locations' },
    provenance: { inputSha256: sha(raw), pythonVersion: '3.11.9', codeSha256: Object.fromEntries(['scripts/statute_diagnostic.py', 'backend/core/statute_check.py', 'backend/core/statute_locate.py', 'backend/api/finance_review.py'].map((name) => [name, 'a'.repeat(64)])) },
    limitations: ['未检出不等于事实有依据。', '不判断法律适用。'] };
}
let requests: ReturnType<typeof vi.fn>;
const savedCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
const savedRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
beforeEach(() => {
  mode.readOnly = false; mode.staticSite = false;
  vi.stubGlobal('crypto', webcrypto);
  requests = vi.fn(async (_url: string, init: RequestInit) => json(reportFor(String(init.body))));
  vi.stubGlobal('fetch', requests);
});
afterEach(() => {
  vi.restoreAllMocks();
  if (savedCreate) Object.defineProperty(URL, 'createObjectURL', savedCreate); else Reflect.deleteProperty(URL, 'createObjectURL');
  if (savedRevoke) Object.defineProperty(URL, 'revokeObjectURL', savedRevoke); else Reflect.deleteProperty(URL, 'revokeObjectURL');
});
const button = () => screen.getByRole('button', { name: '开始核对' });
function example(name = '正常引用') {
  fireEvent.click(screen.getByRole('button', { name }));
  fireEvent.click(screen.getByRole('checkbox', { name: /我确认原文符合/ }));
}
async function complete() { example(); fireEvent.click(button()); await screen.findByText('规则未检出，仍需核对'); }
const blobText = (blob: Blob): Promise<string> => new Promise((resolve, reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(blob);
});

describe('金融引用核对', () => {
  it('可通过独立桌面深链打开', () => {
    expect(parseLabHash('#/lab/finance-review')).toEqual({ target: 'lab', view: 'finance-review' });
  });

  it('确认明示行结构后才发请求，并保留原文和未检出边界', async () => {
    render(<FinanceReviewView/>);
    expect(button()).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '正常引用' }));
    expect(button()).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认原文符合/ }));
    fireEvent.click(button());
    expect(await screen.findByText('规则未检出，仍需核对')).toBeVisible();
    const raw = String(requests.mock.calls[0][1].body);
    const sent = JSON.parse(raw);
    expect(requests.mock.calls[0][0]).toBe('/api/finance-review');
    expect(sent.contexts).toEqual([sent.source]);
    expect(sent.question).toBe('');
    expect(sent.engine).toBe('base');
    expect(sent.sourceFormat).toBe('article-lines-v1');
    expect(screen.getByLabelText('制度原文')).toHaveValue(sent.source);
    expect(screen.getByText(/未提供独立款项结构参照/)).toBeVisible();
    expect(screen.queryByText(/PASS|已证实|自动通过/)).not.toBeInTheDocument();
  });

  it('展示既有规则发现和待核对数字', async () => {
    requests.mockImplementation(async (_url: string, init: RequestInit) => json(reportFor(String(init.body), 'findings')));
    render(<FinanceReviewView/>); example('期限错误'); fireEvent.click(button());
    expect(await screen.findByText('检出规则问题')).toBeVisible();
    expect(screen.getByText('QUANTITY_UNSUPPORTED')).toBeVisible();
    expect(screen.getByText('14个工作日', { selector: 'blockquote' })).toBeVisible();
    expect((screen.getByLabelText('制度原文') as HTMLTextAreaElement).value).toContain('7个工作日');
  });

  it('费率样例展示缺少依据的发现，不据此判断现实费率为假', async () => {
    requests.mockImplementation(async (_url: string, init: RequestInit) => json(reportFor(String(init.body), 'findings')));
    render(<FinanceReviewView/>); example('缺少具体费率依据'); fireEvent.click(button());
    await screen.findByText('检出规则问题');
    expect(screen.getByText('0.1%', { selector: 'blockquote' })).toBeVisible();
    expect((screen.getByLabelText('制度原文') as HTMLTextAreaElement).value).toContain('第九条');
    expect(screen.getByText(/不能据此判断现实费率为假/)).toBeVisible();
  });

  it('修改文本和切换样例都撤下旧报告', async () => {
    render(<FinanceReviewView/>); await complete();
    fireEvent.change(screen.getByLabelText('待核对文本'), { target: { value: '另一段文本。' } });
    expect(screen.queryByText('规则未检出，仍需核对')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载复核包' })).not.toBeInTheDocument();
    fireEvent.click(button()); await screen.findByText('规则未检出，仍需核对');
    fireEvent.click(screen.getByRole('button', { name: '期限错误' }));
    expect(screen.queryByText('规则未检出，仍需核对')).not.toBeInTheDocument();
    expect(button()).toBeDisabled();
  });

  it('网络失败撤下旧结果，保留输入供检查', async () => {
    render(<FinanceReviewView/>); await complete();
    requests.mockRejectedValue(new TypeError('offline'));
    fireEvent.click(button());
    expect(await screen.findByRole('alert')).toHaveTextContent('未取得核对结果');
    expect(screen.queryByText('规则未检出，仍需核对')).not.toBeInTheDocument();
    expect((screen.getByLabelText('制度原文') as HTMLTextAreaElement).value).toContain('第十九条');
  });

  it('编辑期间的迟到响应不能恢复旧报告', async () => {
    let resolve!: (response: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
    render(<FinanceReviewView/>); example(); fireEvent.click(button());
    const raw = String(requests.mock.calls[0][1].body);
    fireEvent.change(screen.getByLabelText('制度原文'), { target: { value: source } });
    await act(async () => { resolve(json(reportFor(raw))); });
    expect(screen.queryByText('规则未检出，仍需核对')).not.toBeInTheDocument();
    expect(screen.getByLabelText('制度原文')).toHaveValue(source);
  });

  it('复核状态展示诊断，不展示未检出结论', async () => {
    requests.mockImplementation(async (_url: string, init: RequestInit) => json(reportFor(String(init.body), 'review')));
    render(<FinanceReviewView/>); example(); fireEvent.click(button());
    expect(await screen.findByText('需要复核，未完成核对')).toBeVisible();
    expect(screen.getByText('NO_PROVISIONS')).toBeVisible();
    expect(screen.queryByText('规则未检出，仍需核对')).not.toBeInTheDocument();
  });

  it('下载绑定同一原始请求文本和报告，并单独导出相同请求字节', async () => {
    const blobs: Blob[] = []; const filenames: string[] = [];
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: (blob: Blob) => { blobs.push(blob); return 'blob:finance'; } });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filenames.push(this.download); });
    render(<FinanceReviewView/>); await complete();
    fireEvent.click(screen.getByRole('button', { name: '下载复核包' }));
    fireEvent.click(screen.getByRole('button', { name: '下载原始请求' }));
    const bundle = JSON.parse(await blobText(blobs[0]));
    const raw = String(requests.mock.calls[0][1].body);
    expect(bundle.requestText).toBe(raw);
    expect(bundle.report.provenance.inputSha256).toBe(sha(raw));
    expect(await blobText(blobs[1])).toBe(raw);
    expect(filenames).toEqual(['finance-review-report.json', 'finance-review-request.json']);
  });

  it('成功下载后再次下载失败会清除旧成功通知，并保留现有报告', async () => {
    const createURL = vi.fn(() => 'blob:finance');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<FinanceReviewView/>); await complete();
    fireEvent.click(screen.getByRole('button', { name: '下载复核包' }));
    expect(screen.getByRole('status')).toHaveTextContent('已触发下载，请在浏览器下载列表确认文件。');

    createURL.mockImplementationOnce(() => { throw new Error('download blocked'); });
    fireEvent.click(screen.getByRole('button', { name: '下载复核包' }));
    expect(screen.getByRole('alert')).toHaveTextContent('下载未完成，请检查浏览器下载设置后重试。');
    expect(screen.queryByText('已触发下载，请在浏览器下载列表确认文件。')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '本次核对结果' })).toBeVisible();
    expect(screen.getByText('规则未检出，仍需核对')).toBeVisible();
    fireEvent.click(screen.getByText('范围、输入绑定与完整输出'));
    expect(screen.getByLabelText('完整规则输出')).toBeVisible();
    expect(screen.getByLabelText('完整规则输出')).toHaveTextContent('"status": "no_error_found"');
  });

  it('拒绝输入哈希不匹配的报告', async () => {
    requests.mockImplementation(async (_url: string, init: RequestInit) => {
      const report = reportFor(String(init.body)); report.provenance.inputSha256 = '0'.repeat(64); return json(report);
    });
    await expect(checkFinanceReview(input)).rejects.toThrow(/绑定/);
  });

  it('以严格UTF-8读取TXT，不用替换字符接受坏编码', async () => {
    await expect(readFinanceFile(new File([source], 'source.txt'))).resolves.toBe(source);
    await expect(readFinanceFile(new File([new Uint8Array([0xff])], 'bad.txt'))).rejects.toThrow(/UTF-8/);
  });

  it.each(['readOnly', 'staticSite'] as const)('公开或静态构建不导入资料或发送请求：%s', async (flag) => {
    mode[flag] = true;
    render(<FinanceReviewView/>);
    expect(button()).toBeDisabled();
    expect(screen.getByLabelText('导入 UTF-8 TXT')).toBeDisabled();
    await expect(checkFinanceReview(input)).rejects.toThrow(/本地/);
    expect(requests).not.toHaveBeenCalled();
  });
});
