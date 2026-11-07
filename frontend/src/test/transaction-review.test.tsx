import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TransactionReviewView } from '../features/TransactionReviewView';
import { labHash, parseLabHash } from '../labRoute';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', async (original) => ({ ...await original<typeof import('../config')>(), get READ_ONLY() { return mode.readOnly; }, get STATIC_SITE() { return mode.staticSite; } }));

// Hand-checked four-row Task 1 contract: t003's actual total is 1600.60,
// so 1601.60 is wrong; t004 lacks amount evidence and cannot prove fraud.
const sourceRequest = {
  schemaVersion: 1, currency: 'CNY',
  transactions: [
    { transaction_id: 't001', timestamp: '2026-11-01T10:00:00+08:00', customer_id: '模拟客户甲', terminal_id: 'm1', amount: '500.10' },
    { transaction_id: 't002', timestamp: '2026-11-01T10:05:00+08:00', customer_id: '模拟客户甲', terminal_id: 'm1', amount: '500.20' },
    { transaction_id: 't003', timestamp: '2026-11-01T10:10:00+08:00', customer_id: '模拟客户甲', terminal_id: 'm2', amount: '600.30' },
    { transaction_id: 't004', timestamp: '2026-11-01T10:12:00+08:00', customer_id: '模拟客户乙', terminal_id: 'm3', amount: '12000.00' },
  ],
  rules: { highAmount: '10000.00', windowMinutes: 10, minTransactions: 3 },
  claims: [
    { transaction_id: 't003', field: 'window_count', value: 3, evidence_ids: ['t001', 't002', 't003'] },
    { transaction_id: 't003', field: 'window_total', value: '1601.60', evidence_ids: ['t001', 't002', 't003'] },
    { transaction_id: 't004', field: 'amount', value: '12000.00', evidence_ids: [] },
    { transaction_id: 't004', field: 'fraud_confirmed', value: true, evidence_ids: ['t004'] },
  ],
};
const checks = [
  { ...sourceRequest.claims[0], expected: 3, required_evidence_ref: { group_id: 'g0002', start: 0, end: 3 }, status: 'consistent', reasons: ['MATCH'] },
  { ...sourceRequest.claims[1], expected: '1600.60', required_evidence_ref: { group_id: 'g0002', start: 0, end: 3 }, status: 'inconsistent', reasons: ['VALUE_MISMATCH'] },
  { ...sourceRequest.claims[2], expected: '12000.00', required_evidence_ids: ['t004'], status: 'review', reasons: ['EVIDENCE_MISSING'] },
  { ...sourceRequest.claims[3], expected: null, status: 'review', reasons: ['FRAUD_NOT_PROVABLE'] },
];
const sourceReport = {
  schemaVersion: 1, engineVersion: 'transaction-review-v1.1', status: 'review', currency: 'CNY', rules: sourceRequest.rules,
  summary: { totalTransactions: 4, flaggedTransactions: 2, consistentClaims: 1, inconsistentClaims: 1, reviewClaims: 2 },
  evidenceIndex: { g0001: { customer_id: '模拟客户乙', transaction_ids: ['t004'] }, g0002: { customer_id: '模拟客户甲', transaction_ids: ['t001', 't002', 't003'] } },
  cases: [
    { id: 't003', transaction: { ...sourceRequest.transactions[2], timestamp: '2026-11-01T02:10:00Z' }, signals: [{ rule: 'window_count', value: 3, threshold: 3, windowMinutes: 10 }], window: { start: '2026-11-01T02:00:00Z', end: '2026-11-01T02:10:00Z', count: 3, total: '1600.60', evidence_ref: { group_id: 'g0002', start: 0, end: 3 } }, claims: checks.slice(0, 2), evidenceStatus: 'inconsistent' },
    { id: 't004', transaction: { ...sourceRequest.transactions[3], timestamp: '2026-11-01T02:12:00Z' }, signals: [{ rule: 'high_amount', value: '12000.00', threshold: '10000.00', unit: 'CNY' }], window: { start: '2026-11-01T02:02:00Z', end: '2026-11-01T02:12:00Z', count: 1, total: '12000.00', evidence_ref: { group_id: 'g0001', start: 0, end: 1 } }, claims: checks.slice(2), evidenceStatus: 'review' },
  ],
  claimChecks: checks,
  limitations: ['仅核对结构化字段和证据，不证明来源真实。'],
  provenance: { requestSha256: '291ee2d345af475581b1ddb16182f074fd15285662c780de4fbbada021d44878', codeSha256: { 'backend/core/transaction_review.py': 'a'.repeat(64), 'backend/api/transaction_review.py': 'b'.repeat(64), 'scripts/transaction_review.py': 'c'.repeat(64) } },
};
const sourceBundle = { schema: 'TransactionReviewBundleV1', request: sourceRequest, report: sourceReport, decisions: [{ transaction_id: 't003', decision: 'need_more', reason: '请补充客户业务背景。' }] };
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const csvHeader = 'transaction_id,timestamp,customer_id,terminal_id,amount';
const csvRow = 't1,2026-11-01T10:00:00.123456+08:00,c1,m1,9007199254740993.01';
let requests: ReturnType<typeof vi.fn>;
const savedCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
const savedRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
beforeEach(() => {
  mode.readOnly = false; mode.staticSite = false;
  requests = vi.fn(async (url: string) => json(url.endsWith('/replay') ? { ...sourceBundle, replayed: true } : sourceReport));
  vi.stubGlobal('fetch', requests);
});
afterEach(() => {
  vi.restoreAllMocks();
  if (savedCreate) Object.defineProperty(URL, 'createObjectURL', savedCreate); else Reflect.deleteProperty(URL, 'createObjectURL');
  if (savedRevoke) Object.defineProperty(URL, 'revokeObjectURL', savedRevoke); else Reflect.deleteProperty(URL, 'revokeObjectURL');
});
function paste(value: string) {
  fireEvent.change(screen.getByLabelText('CSV / JSON 输入'), { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: '读取粘贴内容' }));
}
function loadRequest(value: unknown = sourceRequest) { paste(JSON.stringify(value)); }
async function analyze() {
  loadRequest();
  fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
  await screen.findByRole('heading', { name: '本次交易复核结果' });
}
const blobText = (blob: Blob): Promise<string> => new Promise((resolve, reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error); reader.readAsText(blob);
});
function captureDownloads() {
  const blobs: Blob[] = []; const names: string[] = [];
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: (blob: Blob) => { blobs.push(blob); return 'blob:transaction'; } });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download); });
  return { blobs, names };
}

describe('交易风险复核桌面入口', () => {
  it('可直接打开交易复核深链并保留演示入口', () => {
    expect(parseLabHash('#/lab/transaction-review')).toEqual({ target: 'lab', view: 'transaction-review' });
    expect(parseLabHash('#/lab/demo/transaction-review')).toEqual({ target: 'demo', view: 'transaction-review' });
    expect(labHash('transaction-review' as Parameters<typeof labHash>[0], false)).toBe('#/lab/transaction-review');
    expect(parseLabHash('#/lab/transaction-review/unknown')).toBeNull();
  });
});

describe('交易风险复核桌面工作流', () => {
  it('明确展示模拟材料，只有显式分析才提交真实接口合同', async () => {
    render(<TransactionReviewView/>);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '模拟边界示例' }));
    expect(screen.getByText(/合成交易.*非真实欺诈标签/)).toBeVisible();
    expect(requests).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    expect(await screen.findByRole('heading', { name: '本次交易复核结果' })).toBeVisible();
    expect(requests.mock.calls[0][0]).toBe('/api/transaction-review/analyze');
    expect(JSON.parse(String(requests.mock.calls[0][1].body))).toEqual(sourceRequest);
    expect(screen.getByText(/高金额可能是正常业务.*未触发线索不等于安全/)).toBeVisible();
    expect(screen.getByRole('heading', { name: '规则线索' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '字段事实核对' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '人工复核决定' })).toBeVisible();
    expect(screen.queryByText(/安全概率|诈骗概率|已认定诈骗/)).not.toBeInTheDocument();
  });

  it('CSV保留大额字符串、六位微秒、引号/逗号/换行并剔除标签列', async () => {
    requests.mockResolvedValue(json({ detail: '工程测试仅检查请求' }, 422));
    render(<TransactionReviewView/>);
    paste(`\uFEFF${csvHeader},label,notes\r\n\r\n"t,1",2026-11-01T10:00:00.123456+08:00,"客""户\n甲",m1,9007199254740993.01,fraud,"x,y"\r\n`);
    expect(screen.getByText(/忽略额外列.*label.*notes/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    const sent = JSON.parse(String(requests.mock.calls[0][1].body));
    expect(sent.transactions).toEqual([{ transaction_id: 't,1', timestamp: '2026-11-01T10:00:00.123456+08:00', customer_id: '客"户\n甲', terminal_id: 'm1', amount: '9007199254740993.01' }]);
    expect(sent).not.toHaveProperty('label');
    expect(sent.transactions[0]).not.toHaveProperty('notes');
  });

  it.each([
    [`transaction_id,timestamp,customer_id,amount\nt1,2026-11-01T10:00:00Z,c1,1`, /缺少.*terminal_id/],
    [`${csvHeader},amount\n${csvRow},1`, /重复.*amount/],
    [`${csvHeader}\n${csvRow},surplus`, /列数/],
    [`${csvHeader}\n"unfinished`, /引号/],
    [`${csvHeader}\n${csvRow}\n${csvRow}`, /重复.*t1/],
  ])('拒绝结构错误CSV，不悄悄跳行：%s', (text, message) => {
    render(<TransactionReviewView/>); paste(text);
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });

  it('JSON数字金额无损变为十进制字符串，未知额外交易字段不静默丢弃', async () => {
    requests.mockResolvedValue(json({ detail: '工程测试仅检查请求' }, 422));
    render(<TransactionReviewView/>);
    paste('{"schemaVersion":1,"currency":"CNY","transactions":[{"transaction_id":"t1","timestamp":"2026-11-01T10:00:00Z","customer_id":"c1","terminal_id":"m1","amount":9007199254740993.01}]}');
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    expect(JSON.parse(String(requests.mock.calls[0][1].body)).transactions[0].amount).toBe('9007199254740993.01');
    paste(JSON.stringify({ ...sourceRequest, transactions: [{ ...sourceRequest.transactions[0], label: 'fraud' }] }));
    expect(screen.getByRole('alert')).toHaveTextContent(/额外字段.*label/);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
  });

  it('科学计数金额仅取正确字段的词元，转义引号与文本中的数字原样保留', async () => {
    requests.mockResolvedValue(json({ detail: '工程测试仅检查请求' }, 422));
    render(<TransactionReviewView/>);
    const text = '{"schemaVersion":1,"currency":"CNY","transactions":[{"transaction_id":"t1","timestamp":"2026-11-01T10:00:00.123456Z","customer_id":"客户 \\"123\\" 与 9007199254740993.01","terminal_id":"m1","amount":9.00719925474099301e15}],"rules":{"highAmount":9.00719925474099301e15,"windowMinutes":10,"minTransactions":3},"claims":[{"transaction_id":"t1","field":"window_total","value":9.00719925474099301e15,"evidence_ids":["t1"]}]}';
    paste(text);
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    const sent = JSON.parse(String(requests.mock.calls[0][1].body));
    expect(sent.transactions[0].amount).toBe('9.00719925474099301e15');
    expect(sent.rules.highAmount).toBe('9.00719925474099301e15');
    expect(sent.claims[0].value).toBe('9.00719925474099301e15');
    expect(sent.transactions[0].customer_id).toBe('客户 "123" 与 9007199254740993.01');
    expect(sent.transactions[0].timestamp).toBe('2026-11-01T10:00:00.123456Z');
    expect(sent.rules.windowMinutes).toBe(10);
  });

  it('JSON重复键包括转义后的同名金额键会拒绝，保留原输入供修正', () => {
    render(<TransactionReviewView/>);
    const text = '{"schemaVersion":1,"currency":"CNY","transactions":[{"transaction_id":"t1","timestamp":"2026-11-01T10:00:00Z","customer_id":"c1","terminal_id":"m1","amount":"1.00","\\u0061mount":"2.00"}]}';
    paste(text);
    expect(screen.getByRole('alert')).toHaveTextContent(/重复.*amount/);
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(text);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });

  it('5001行和超过2MiB原始请求明确拒绝，不截断后提交', () => {
    render(<TransactionReviewView/>);
    paste(`${csvHeader}\n${Array.from({ length: 5001 }, (_, index) => csvRow.replace('t1,', `t${index},`)).join('\n')}`);
    expect(screen.getByRole('alert')).toHaveTextContent(/5000/);
    paste(`{"schemaVersion":1,"currency":"CNY","transactions":[],"padding":"${'x'.repeat(2 * 1024 * 1024)}"}`);
    expect(screen.getByRole('alert')).toHaveTextContent(/2 MiB/);
    expect(requests).not.toHaveBeenCalled();
  });

  it('文件严格UTF-8读取，导入请求不自动请求，坏编码保留粘贴输入', async () => {
    render(<TransactionReviewView/>);
    const upload = screen.getByLabelText('导入交易 CSV / JSON / 复核包');
    fireEvent.change(upload, { target: { files: [new File([`\uFEFF${csvHeader}\r\n${csvRow}`], 'sample.csv')] } });
    await waitFor(() => expect(screen.getByRole('button', { name: '分析并核对' })).toBeEnabled());
    expect(requests).not.toHaveBeenCalled();
    const text = (screen.getByLabelText('CSV / JSON 输入') as HTMLTextAreaElement).value;
    fireEvent.change(upload, { target: { files: [new File([new Uint8Array([0xff])], 'bad.csv')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent(/UTF-8/);
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(text);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
  });

  it('证据切片解析出实际来源行并单独显示不一致与缺证事实', async () => {
    render(<TransactionReviewView/>); await analyze();
    const evidence = screen.getByRole('table', { name: '所选案件的来源交易' });
    expect(within(evidence).getAllByRole('row')).toHaveLength(4);
    expect(within(evidence).getByText('t001')).toBeVisible();
    expect(within(evidence).getByText('500.20')).toBeVisible();
    expect(within(evidence).queryByText('t004')).not.toBeInTheDocument();
    expect(screen.getByText('1600.60', { selector: '[data-expected]' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '选择案件 t004' }));
    expect(screen.getByText(/缺少引用证据/)).toBeVisible();
    expect(screen.getByText(/交易记录不能证明欺诈/)).toBeVisible();
    expect(within(screen.getByRole('table', { name: '所选案件的来源交易' })).getByText('t004')).toBeVisible();
  });

  it('从报告载入说明可改错值和错引用，重提时不携带报告check附加字段', async () => {
    render(<TransactionReviewView/>); await analyze();
    fireEvent.click(screen.getByRole('button', { name: '编辑说明 t003 window_total' }));
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('说明值')).toHaveValue('1601.60');
    fireEvent.change(screen.getByLabelText('说明值'), { target: { value: '1600.60' } });
    fireEvent.change(screen.getByLabelText('引用交易 ID（JSON 数组）'), { target: { value: '["t004"]' } });
    fireEvent.change(screen.getByLabelText('引用交易 ID（JSON 数组）'), { target: { value: '["t001","t002","t003"]' } });
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    const sent = JSON.parse(String(requests.mock.calls.at(-1)![1].body));
    expect(sent.claims[1]).toEqual({ transaction_id: 't003', field: 'window_total', value: '1600.60', evidence_ids: ['t001', 't002', 't003'] });
    expect(Object.keys(sent.claims[1]).sort()).toEqual(['evidence_ids', 'field', 'transaction_id', 'value']);
  });

  it('说明主体/字段/值和索引引用均可编辑，规则编辑立即撤下报告与人工决定', async () => {
    render(<TransactionReviewView/>); await analyze();
    fireEvent.change(screen.getByLabelText('人工复核决定'), { target: { value: 'escalate' } });
    fireEvent.change(screen.getByLabelText('决定原因'), { target: { value: '请调查关联业务。' } });
    fireEvent.change(screen.getByLabelText('大额阈值'), { target: { value: '11000.00' } });
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('说明交易 ID'), { target: { value: 't004' } });
    fireEvent.change(screen.getByLabelText('说明字段'), { target: { value: 'window_count' } });
    fireEvent.change(screen.getByLabelText('说明值'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('引用方式'), { target: { value: 'slice' } });
    fireEvent.change(screen.getByLabelText('证据组 ID'), { target: { value: 'g0001' } });
    fireEvent.change(screen.getByLabelText('切片起点'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('切片终点（不含）'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    const sent = JSON.parse(String(requests.mock.calls.at(-1)![1].body));
    expect(sent.rules.highAmount).toBe('11000.00');
    expect(sent.claims[0]).toEqual({ transaction_id: 't004', field: 'window_count', value: 1, evidence_ref: { group_id: 'g0001', start: 0, end: 1 } });
    expect(screen.getByLabelText('人工复核决定')).toHaveValue('pending');
    expect(screen.getByLabelText('决定原因')).toHaveValue('');
  });

  it('编辑或取消会终止请求，迟到响应不能恢复旧报告', async () => {
    let respond!: (response: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((resolve) => { respond = resolve; }));
    render(<TransactionReviewView/>); loadRequest(); fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    const signal = requests.mock.calls[0][1].signal as AbortSignal;
    fireEvent.change(screen.getByLabelText('单一币种'), { target: { value: 'USD' } });
    expect(signal.aborted).toBe(true);
    await act(async () => { respond(json(sourceReport)); });
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    fireEvent.click(screen.getByRole('button', { name: '取消当前操作' }));
    await act(async () => { respond(json(sourceReport)); });
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
  });

  it('编辑取消未完成文件读取，迟到文件内容不会覆盖新输入', async () => {
    let reader!: FileReader;
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this; });
    render(<TransactionReviewView/>);
    fireEvent.change(screen.getByLabelText('导入交易 CSV / JSON / 复核包'), { target: { files: [new File([csvRow], 'late.csv')] } });
    fireEvent.change(screen.getByLabelText('CSV / JSON 输入'), { target: { value: '当前新输入' } });
    await act(async () => {
      Object.defineProperty(reader, 'result', { value: new TextEncoder().encode(`${csvHeader}\n${csvRow}`).buffer });
      reader.onload?.(new ProgressEvent('load') as ProgressEvent<FileReader>);
    });
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue('当前新输入');
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });

  it('不带原因的人工决定不能导出，补全后紧凑下载请求/完整报告/所有决定', async () => {
    const downloads = captureDownloads();
    render(<TransactionReviewView/>); await analyze();
    fireEvent.change(screen.getByLabelText('人工复核决定'), { target: { value: 'dismiss' } });
    fireEvent.click(screen.getByRole('button', { name: '下载完整复核包' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/t003.*原因/);
    expect(downloads.blobs).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('决定原因'), { target: { value: '正常业务，继续保留交易来源。' } });
    fireEvent.click(screen.getByRole('button', { name: '下载完整复核包' }));
    const raw = await blobText(downloads.blobs[0]); const bundle = JSON.parse(raw);
    expect(raw).toBe(JSON.stringify(bundle));
    expect(bundle.schema).toBe('TransactionReviewBundleV1');
    expect(bundle.request).toEqual(sourceRequest);
    expect(bundle.report).toEqual(sourceReport);
    expect(bundle.decisions).toEqual([{ transaction_id: 't003', decision: 'dismiss', reason: '正常业务，继续保留交易来源。' }, { transaction_id: 't004', decision: 'pending', reason: '' }]);
    expect(downloads.names).toEqual(['transaction-review-bundle.json']);
    expect(screen.getByText(/不验证决定人的身份/)).toBeVisible();
  });

  it('下载失败说明浏览器错误，保留报告和输入供显式重试', async () => {
    captureDownloads(); render(<TransactionReviewView/>); await analyze();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => { throw new Error('blocked'); } });
    fireEvent.click(screen.getByRole('button', { name: '下载完整复核包' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/下载未完成/);
    expect(screen.getByRole('heading', { name: '本次交易复核结果' })).toBeVisible();
  });

  it.each(['constructor', '__proto__', 'toString'])('原型名交易ID决定回归：%s 可首次分析、独立保存、完整下载并重开', async (firstId) => {
    const ids = [firstId, ...['constructor', '__proto__', 'toString'].filter((id) => id !== firstId)];
    const transactions = ids.map((id, index) => ({ ...sourceRequest.transactions[3], transaction_id: id, customer_id: `模拟主体${index}` }));
    const request = { ...copy(sourceRequest), transactions, claims: [] };
    const report = {
      ...copy(sourceReport),
      summary: { totalTransactions: 3, flaggedTransactions: 3, consistentClaims: 0, inconsistentClaims: 0, reviewClaims: 0 },
      evidenceIndex: Object.fromEntries(transactions.map((row, index) => [`g${index}`, { customer_id: row.customer_id, transaction_ids: [row.transaction_id] }])),
      cases: transactions.map((row, index) => ({
        ...copy(sourceReport.cases[1]), id: row.transaction_id, transaction: row,
        window: { ...sourceReport.cases[1].window, evidence_ref: { group_id: `g${index}`, start: 0, end: 1 } }, claims: [], evidenceStatus: 'review',
      })),
      claimChecks: [],
    };
    requests.mockResolvedValueOnce(json(report));
    const downloads = captureDownloads();
    render(<TransactionReviewView/>); loadRequest(request);
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    expect(screen.getByLabelText('人工复核决定')).toHaveValue('pending');
    expect(screen.getByLabelText('决定原因')).toHaveValue('');

    fireEvent.click(screen.getByRole('button', { name: '下载完整复核包' }));
    expect(JSON.parse(await blobText(downloads.blobs[0]))).toEqual({
      schema: 'TransactionReviewBundleV1', request, report,
      decisions: ids.map((transaction_id) => ({ transaction_id, decision: 'pending', reason: '' })),
    });

    const values = ['escalate', 'dismiss', 'need_more'] as const;
    const decisions = ids.map((transaction_id, index) => ({ transaction_id, decision: values[index], reason: `${transaction_id} 的独立人工原因` }));
    for (const item of decisions) {
      const button = screen.getByRole('button', { name: `选择案件 ${item.transaction_id}` });
      expect(button).toHaveTextContent('待复核');
      fireEvent.click(button);
      expect(screen.getByLabelText('人工复核决定')).toHaveValue('pending');
      expect(screen.getByLabelText('决定原因')).toHaveValue('');
      fireEvent.change(screen.getByLabelText('人工复核决定'), { target: { value: item.decision } });
      fireEvent.change(screen.getByLabelText('决定原因'), { target: { value: item.reason } });
      expect(button).not.toHaveTextContent('待复核');
    }
    for (const item of decisions) {
      fireEvent.click(screen.getByRole('button', { name: `选择案件 ${item.transaction_id}` }));
      expect(screen.getByLabelText('人工复核决定')).toHaveValue(item.decision);
      expect(screen.getByLabelText('决定原因')).toHaveValue(item.reason);
    }
    fireEvent.click(screen.getByRole('button', { name: '下载完整复核包' }));
    const raw = await blobText(downloads.blobs[1]);
    const bundle = { schema: 'TransactionReviewBundleV1', request, report, decisions };
    expect(raw).toBe(JSON.stringify(bundle));
    expect(JSON.parse(raw)).toEqual(bundle);

    requests.mockResolvedValueOnce(json({ ...bundle, replayed: true }));
    paste(raw);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重开并验证' }));
    expect(await screen.findByText('服务重开验证通过')).toBeVisible();
    expect(requests).toHaveBeenCalledTimes(2);
    expect(requests.mock.calls[1][0]).toBe('/api/transaction-review/replay');
    expect(requests.mock.calls[1][1].body).toBe(raw);
    for (const item of decisions) {
      fireEvent.click(screen.getByRole('button', { name: `选择案件 ${item.transaction_id}` }));
      expect(screen.getByLabelText('人工复核决定')).toHaveValue(item.decision);
      expect(screen.getByLabelText('决定原因')).toHaveValue(item.reason);
    }
    fireEvent.click(screen.getByRole('button', { name: '下载完整复核包' }));
    expect(JSON.parse(await blobText(downloads.blobs[2]))).toEqual(bundle);
    expect(downloads.names).toEqual(Array(3).fill('transaction-review-bundle.json'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('重开先等待服务复算，把整个包发送replay且仅显示服务验证后的决定', async () => {
    let respond!: (response: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((resolve) => { respond = resolve; }));
    render(<TransactionReviewView/>); paste(JSON.stringify(sourceBundle));
    expect(requests).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重开并验证' }));
    expect(requests.mock.calls[0][0]).toBe('/api/transaction-review/replay');
    expect(JSON.parse(String(requests.mock.calls[0][1].body))).toEqual(sourceBundle);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    await act(async () => { respond(json({ ...sourceBundle, replayed: true })); });
    expect(await screen.findByText('服务重开验证通过')).toBeVisible();
    expect(screen.getByLabelText('人工复核决定')).toHaveValue('need_more');
    expect(screen.getByLabelText('决定原因')).toHaveValue('请补充客户业务背景。');
  });

  it('重开直接发送完整上传JSON字节文本，原request大数不经浏览器重造', async () => {
    requests.mockResolvedValue(json({ detail: '报告不匹配' }, 422));
    const raw = JSON.stringify(sourceBundle, null, 2).replace('"500.10"', '9007199254740993.01');
    render(<TransactionReviewView/>); paste(raw);
    fireEvent.click(screen.getByRole('button', { name: '重开并验证' }));
    await screen.findByRole('alert');
    expect(requests.mock.calls[0][1].body).toBe(raw);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
  });

  it.each(['报告内容与当前重算不一致', '代码绑定已改变，请重新分析', '非待复核决定必须填写原因'])('重开失败不采信旧报告并显示原因：%s', async (detail) => {
    requests.mockResolvedValue(json({ detail }, 422));
    render(<TransactionReviewView/>); paste(JSON.stringify(sourceBundle));
    fireEvent.click(screen.getByRole('button', { name: '重开并验证' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(detail);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重开并验证' })).toBeEnabled();
  });

  it('不把裸报告当作已验证复核包，嵌入请求仍受2MiB限制', () => {
    render(<TransactionReviewView/>); paste(JSON.stringify(sourceReport));
    expect(screen.getByRole('alert')).toHaveTextContent(/交易请求.*复核包/);
    paste(JSON.stringify({ ...sourceBundle, request: { ...sourceRequest, transactions: [{ ...sourceRequest.transactions[0], customer_id: 'x'.repeat(2 * 1024 * 1024) }] } }));
    expect(screen.getByRole('alert')).toHaveTextContent(/2 MiB/);
    expect(requests).not.toHaveBeenCalled();
  });

  it('重跑的网络/响应JSON错误保留请求，旧报告不会留为当前结果', async () => {
    render(<TransactionReviewView/>); await analyze();
    requests.mockRejectedValueOnce(new TypeError('offline'));
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/本地.*连接/);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(JSON.stringify(sourceRequest));
    requests.mockResolvedValueOnce(new Response('<html>bad proxy</html>', { status: 200 }));
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/JSON/);
    expect(requests).toHaveBeenCalledTimes(3);
  });

  it('服务报告缺少统计字段时不把缺失值显示为空白或零', async () => {
    const malformed = copy(sourceReport);
    Reflect.deleteProperty(malformed.summary, 'consistentClaims');
    requests.mockResolvedValue(json(malformed));
    render(<TransactionReviewView/>); loadRequest();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/报告结构/);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(JSON.stringify(sourceRequest));
  });

  it('重开离线不显示上传报告，保留复核包供重试', async () => {
    requests.mockRejectedValueOnce(new TypeError('offline'));
    render(<TransactionReviewView/>); paste(JSON.stringify(sourceBundle));
    fireEvent.click(screen.getByRole('button', { name: '重开并验证' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/本地.*连接/);
    expect(screen.queryByRole('heading', { name: '本次交易复核结果' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重开并验证' })).toBeEnabled();
  });

  it('5000案件保留全局分母并分页，DOM只展开当前案件和当前页', async () => {
    const transactions = Array.from({ length: 5000 }, (_, index) => ({ transaction_id: `s${String(index).padStart(4, '0')}`, timestamp: '2026-11-01T10:00:00Z', customer_id: `c${index}`, terminal_id: 'm1', amount: '20000.00' }));
    const claim = { transaction_id: 's0000', field: 'amount', value: '20000.00', evidence_ids: ['s0000'] };
    const fact = { ...claim, expected: '20000.00', required_evidence_ids: ['s0000'], status: 'consistent', reasons: ['MATCH'] };
    const report = { ...sourceReport,
      summary: { totalTransactions: 5000, flaggedTransactions: 5000, consistentClaims: 1, inconsistentClaims: 0, reviewClaims: 0 },
      evidenceIndex: Object.fromEntries(transactions.map((row, index) => [`g${index}`, { customer_id: row.customer_id, transaction_ids: [row.transaction_id] }])),
      cases: transactions.map((row, index) => ({ id: row.transaction_id, transaction: row, signals: [{ rule: 'high_amount', value: '20000.00', threshold: '10000.00', unit: 'CNY' }], window: { start: '2026-11-01T09:50:00Z', end: row.timestamp, count: 1, total: '20000.00', evidence_ref: { group_id: `g${index}`, start: 0, end: 1 } }, claims: index === 0 ? [fact] : [], evidenceStatus: index === 0 ? 'consistent' : 'review' })),
      claimChecks: [fact],
    };
    requests.mockResolvedValue(json(report));
    const { container } = render(<TransactionReviewView/>);
    loadRequest({ ...sourceRequest, transactions, claims: [claim] });
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    expect(screen.getByText('总计 5000 笔 · 线索 5000 笔')).toBeVisible();
    expect(screen.getAllByRole('button', { name: /^选择案件 / })).toHaveLength(25);
    expect(screen.queryByRole('button', { name: '选择案件 s0025' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页案件' }));
    expect(screen.getByRole('button', { name: '选择案件 s0025' })).toBeVisible();
    fireEvent.change(screen.getByLabelText('案件页码'), { target: { value: '200' } });
    expect(screen.getByRole('button', { name: '选择案件 s4999' })).toBeVisible();
    expect(container.querySelectorAll('*').length).toBeLessThan(1200);
  });

  it('5000条显式说明仍只编辑一条，可直接定位最后一条而不创建5000个选项', () => {
    const transactions = Array.from({ length: 5000 }, (_, index) => ({ transaction_id: `c${index}`, timestamp: '2026-11-01T10:00:00Z', customer_id: 'same-customer', terminal_id: 'm1', amount: '1.00' }));
    const claims = transactions.map((row) => ({ transaction_id: row.transaction_id, field: 'amount', value: '1.00', evidence_ids: [row.transaction_id] }));
    const { container } = render(<TransactionReviewView/>);
    loadRequest({ ...sourceRequest, transactions, claims });
    expect(container.querySelectorAll('*').length).toBeLessThan(1200);
    fireEvent.click(screen.getByText('结构化说明 · 5000 条'));
    fireEvent.change(screen.getByLabelText('说明序号'), { target: { value: '5000' } });
    expect(screen.getByLabelText('说明交易 ID')).toHaveValue('c4999');
    expect(requests).not.toHaveBeenCalled();
  });

  it.each(['readOnly', 'staticSite'] as const)('只读/静态模式禁止导入分析及重开：%s', (flag) => {
    mode[flag] = true; render(<TransactionReviewView/>);
    expect(screen.getByText(/请在本地服务中使用/)).toBeVisible();
    expect(screen.getByLabelText('导入交易 CSV / JSON / 复核包')).toBeDisabled();
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '读取粘贴内容' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重开并验证' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });
});

describe('第二轮回归', () => {
  const integerRequest = { ...sourceRequest, claims: [{ transaction_id: 't003', field: 'window_count', value: 3, evidence_ref: { group_id: 'g0002', start: 1, end: 3 } }] };
  const integerPositions = [
    ['schemaVersion', '"schemaVersion":1', '1.0000000000000001', '1.0', '1e0'],
    ['rules.windowMinutes', '"windowMinutes":10', '10.0000000000000001', '10.0', '1e1'],
    ['rules.minTransactions', '"minTransactions":3', '3.0000000000000001', '3.0', '3e0'],
    ['window_count', '"field":"window_count","value":3', '3.0000000000000001', '3.0', '3e0'],
    ['evidence_ref.start', '"start":1', '1.0000000000000001', '1.0', '1e0'],
    ['evidence_ref.end', '"end":3', '3.0000000000000001', '3.0', '3e0'],
  ];
  it.each(integerPositions.flatMap(([label, needle, rounded, decimal, scientific]) => [rounded, decimal, scientific, '9007199254740993'].map((token) => [label, token, needle])))('JSON整数 %s 拒绝原词元 %s，保留原文且不提交', (_label, token, needle) => {
    const text = JSON.stringify(integerRequest).replace(needle, `${needle.slice(0, needle.lastIndexOf(':') + 1)}${token}`);
    render(<TransactionReviewView/>); paste(text);
    expect(screen.getByRole('alert')).toHaveTextContent(/整数|schemaVersion|window_count|索引切片/);
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(text);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });

  it.each(['request', 'report'].flatMap((location) => ['1.0000000000000001', '1.0', '1e0', '9007199254740993'].map((token) => [location, token])))('复核包 %s.schemaVersion 拒绝原词元 %s', (location, token) => {
    const text = JSON.stringify(sourceBundle).replace(`"${location}":{"schemaVersion":1`, `"${location}":{"schemaVersion":${token}`);
    render(<TransactionReviewView/>); paste(text);
    expect(screen.getByRole('alert')).toHaveTextContent(/整数|schemaVersion|window_count|索引切片/);
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(text);
    expect(screen.getByRole('button', { name: '重开并验证' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });

  it('JSON标准整数和合法负零起点仍可提交，金额与原始时间不变', async () => {
    requests.mockResolvedValueOnce(json({ detail: '本例只检查送出的请求' }, 422));
    const text = JSON.stringify(integerRequest).replace('"start":1', '"start":-0');
    render(<TransactionReviewView/>); paste(text);
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    expect(JSON.parse(String(requests.mock.calls[0][1].body))).toEqual({ ...integerRequest, claims: [{ ...integerRequest.claims[0], evidence_ref: { group_id: 'g0002', start: 0, end: 3 } }] });
  });

  it('CSV物理空白行仍可跳过并保留全部有效交易', async () => {
    requests.mockResolvedValueOnce(json({ detail: '本例只检查送出的请求' }, 422));
    render(<TransactionReviewView/>); paste(`\r\n${csvHeader}\r\n\r\n${csvRow}\r\n\r\n`);
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    expect(JSON.parse(String(requests.mock.calls[0][1].body)).transactions).toEqual([{ transaction_id: 't1', timestamp: '2026-11-01T10:00:00.123456+08:00', customer_id: 'c1', terminal_id: 'm1', amount: '9007199254740993.01' }]);
  });
  it.each(['', '\r\n'])('CSV引号空记录不是物理空白行，必须报列数错误：结束符 %j', (ending) => {
    const text = `${csvHeader}\n${csvRow}\n""${ending}`;
    render(<TransactionReviewView/>); paste(text);
    expect(screen.getByRole('alert')).toHaveTextContent(/列数为 1.*表头为 5/);
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(text.replace(/\r\n?/g, '\n'));
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });

  const lowRequest = { ...sourceRequest, transactions: [{ ...sourceRequest.transactions[0], transaction_id: 'no-risk', amount: '1.00' }], claims: [{ transaction_id: 'no-risk', field: 'amount', value: '2.00', evidence_ids: ['no-risk'] }] };
  const lowCheck = { ...lowRequest.claims[0], expected: '1.00', required_evidence_ids: ['no-risk'], status: 'inconsistent', reasons: ['VALUE_MISMATCH'] };
  const lowReport = { ...sourceReport, summary: { totalTransactions: 1, flaggedTransactions: 0, consistentClaims: 0, inconsistentClaims: 1, reviewClaims: 0 }, cases: [], evidenceIndex: { g0001: { customer_id: lowRequest.transactions[0].customer_id, transaction_ids: ['no-risk'] } }, claimChecks: [lowCheck] };

  it('零规则案件的错金额说明仍能打开实际引用和所需来源行', async () => {
    requests.mockResolvedValueOnce(json(lowReport));
    render(<TransactionReviewView/>); loadRequest(lowRequest);
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    expect(screen.getByText('总计 1 笔 · 线索 0 笔')).toBeVisible();
    expect(screen.queryByLabelText('人工复核决定')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('全部字段检查 · 1 条（含未产生案件的交易）'));
    fireEvent.click(await screen.findByRole('button', { name: '查看引用 no-risk amount' }));
    const table = screen.getByRole('table', { name: '字段检查的来源交易' });
    expect(within(table).getByText('no-risk')).toBeVisible();
    expect(within(table).getByText('1.00')).toBeVisible();
    expect(within(table).queryByText('2.00')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看所需证据 no-risk amount' }));
    expect(within(screen.getByRole('table', { name: '字段检查的来源交易' })).getByText('1.00')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '关闭引用来源' }));
    expect(screen.queryByRole('table', { name: '字段检查的来源交易' })).not.toBeInTheDocument();
  });

  it('零案件未知引用和未知说明的空引用都显示明确结果，所需证据仍可查看', async () => {
    const claims = [{ transaction_id: 'no-risk', field: 'amount', value: '1.00', evidence_ids: ['missing-source'] }, { transaction_id: 'unknown-claim', field: 'amount', value: '2.00', evidence_ids: [] }];
    const report = { ...lowReport, summary: { ...lowReport.summary, inconsistentClaims: 0, reviewClaims: 2 }, claimChecks: [
      { ...claims[0], expected: '1.00', required_evidence_ids: ['no-risk'], status: 'review', reasons: ['EVIDENCE_UNKNOWN'] },
      { ...claims[1], expected: null, status: 'review', reasons: ['TRANSACTION_UNKNOWN'] },
    ] };
    requests.mockResolvedValueOnce(json(report));
    render(<TransactionReviewView/>); loadRequest({ ...lowRequest, claims });
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    fireEvent.click(screen.getByText('全部字段检查 · 2 条（含未产生案件的交易）'));
    fireEvent.click(await screen.findByRole('button', { name: '查看引用 no-risk amount' }));
    const table = screen.getByRole('table', { name: '字段检查的来源交易' });
    expect(within(table).getByText('missing-source')).toBeVisible();
    expect(within(table).getByText('本次原始输入中没有此 ID，缺失字段未填零。')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '查看所需证据 no-risk amount' }));
    expect(within(screen.getByRole('table', { name: '字段检查的来源交易' })).getByText('no-risk')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '查看引用 unknown-claim amount' }));
    expect(screen.getByText('没有提供这项引用的来源行。')).toBeVisible();
    expect(within(screen.getByRole('table', { name: '字段检查的来源交易' })).getAllByRole('row')).toHaveLength(1);
  });

  it('零案件的共享切片来源保持25条分页且切换引用重置页码', async () => {
    const transactions = Array.from({ length: 26 }, (_, index) => ({ ...lowRequest.transactions[0], transaction_id: `low-${String(index).padStart(2, '0')}` }));
    const ids = transactions.map((row) => row.transaction_id);
    const rules = { ...sourceRequest.rules, minTransactions: 27 };
    const claim = { transaction_id: 'low-25', field: 'window_count', value: 27, evidence_ids: ids };
    const check = { ...claim, expected: 26, required_evidence_ref: { group_id: 'g0001', start: 0, end: 26 }, status: 'inconsistent', reasons: ['VALUE_MISMATCH'] };
    const report = { ...lowReport, rules, summary: { ...lowReport.summary, totalTransactions: 26 }, evidenceIndex: { g0001: { customer_id: transactions[0].customer_id, transaction_ids: ids } }, claimChecks: [check] };
    requests.mockResolvedValueOnce(json(report));
    render(<TransactionReviewView/>); loadRequest({ ...lowRequest, transactions, rules, claims: [claim] });
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('heading', { name: '本次交易复核结果' });
    fireEvent.click(screen.getByText('全部字段检查 · 1 条（含未产生案件的交易）'));
    fireEvent.click(await screen.findByRole('button', { name: '查看所需证据 low-25 window_count' }));
    const table = screen.getByRole('table', { name: '字段检查的来源交易' });
    expect(within(table).getAllByRole('row')).toHaveLength(26);
    expect(within(table).queryByText('low-25')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页来源交易' }));
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    expect(within(table).getByText('low-25')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '查看引用 low-25 window_count' }));
    expect(screen.getByLabelText('来源交易页码')).toHaveValue(1);
    expect(within(table).getAllByRole('row')).toHaveLength(26);
  });
});

describe('最终币种回归', () => {
  it.each([
    { name: '混合 USD/CNY', column: 'currency', currencies: ['USD', 'CNY'] },
    { name: '统一 CNY', column: 'currency', currencies: ['CNY', 'CNY'] },
    { name: '统一 USD', column: 'currency', currencies: ['USD', 'USD'] },
    { name: '列名大小写与首尾空白', column: ' \tCuRrEnCy \t', currencies: ['USD', 'CNY'] },
    { name: '币种列值全部为空', column: 'currency', currencies: ['', ''] },
    { name: '含币种表头但没有交易行', column: 'currency', currencies: [] },
  ])('CSV保留币种列拒绝导入：$name', ({ column, currencies }) => {
    const rows = currencies.map((currency, index) => `currency-${index},2026-11-01T10:00:00Z,same-customer,m1,6000.00,${currency}`);
    const text = [`${csvHeader},${column}`, ...rows].join('\n');
    render(<TransactionReviewView/>); paste(text);
    const error = screen.getByRole('alert');
    expect(error).toHaveTextContent('currency');
    expect(error).toHaveTextContent('按币种分批');
    expect(error).toHaveTextContent('单一币种');
    expect(error).toHaveTextContent('JSON');
    expect(screen.getByLabelText('CSV / JSON 输入')).toHaveValue(text);
    expect(screen.queryByText(/忽略额外列/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    expect(requests).not.toHaveBeenCalled();
  });

  it('CSV普通label、fraud_label和notes额外列仍可忽略，不改变交易金额', async () => {
    requests.mockResolvedValueOnce(json({ detail: '本例只检查送出的请求' }, 422));
    render(<TransactionReviewView/>);
    paste(`${csvHeader},label,fraud_label,notes\nt1,2026-11-01T10:00:00Z,c1,m1,6000.00,manual-label,fraud,工程备注`);
    expect(screen.getByText(/忽略额外列.*label.*fraud_label.*notes/)).toBeVisible();
    expect(screen.getByRole('button', { name: '分析并核对' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    expect(requests).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(requests.mock.calls[0][1].body))).toEqual({
      schemaVersion: 1, currency: 'CNY', rules: sourceRequest.rules, claims: [],
      transactions: [{ transaction_id: 't1', timestamp: '2026-11-01T10:00:00Z', customer_id: 'c1', terminal_id: 'm1', amount: '6000.00' }],
    });
  });

  it('JSON顶层USD单一币种仍原样提交，不改成CNY', async () => {
    requests.mockResolvedValueOnce(json({ detail: '本例只检查送出的请求' }, 422));
    const request = { ...copy(sourceRequest), currency: 'USD' };
    render(<TransactionReviewView/>); loadRequest(request);
    expect(screen.getByLabelText('单一币种')).toHaveValue('USD');
    fireEvent.click(screen.getByRole('button', { name: '分析并核对' }));
    await screen.findByRole('alert');
    expect(requests).toHaveBeenCalledTimes(1);
    expect(requests.mock.calls[0][0]).toBe('/api/transaction-review/analyze');
    expect(JSON.parse(String(requests.mock.calls[0][1].body))).toEqual(request);
  });
});
