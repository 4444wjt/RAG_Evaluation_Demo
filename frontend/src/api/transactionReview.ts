import { READ_ONLY, STATIC_SITE } from '../config';
import { sessionHeaders } from './session';

export const TRANSACTION_MAX_BYTES = 2 * 1024 * 1024;
export const TRANSACTION_BUNDLE_MAX_BYTES = 24 * 1024 * 1024;
export const TRANSACTION_MAX_ROWS = 5000;
export const TRANSACTION_FIELDS = ['transaction_id', 'timestamp', 'customer_id', 'terminal_id', 'amount'] as const;
export const CLAIM_FIELDS = ['amount', 'customer_id', 'terminal_id', 'timestamp', 'window_count', 'window_total', 'fraud_confirmed'] as const;
const LOCAL = '公开演示和静态页面不接收交易资料；请在本地服务中使用此入口。';
const bytes = (value: string) => new TextEncoder().encode(value).length;
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export type Transaction = { transaction_id: string; timestamp: string; customer_id: string; terminal_id: string; amount: string };
export type TransactionRules = { highAmount: string; windowMinutes: number; minTransactions: number };
export type EvidenceRef = { group_id: string; start: number; end: number };
export type ClaimField = typeof CLAIM_FIELDS[number];
export type TransactionClaim = { transaction_id: string; field: ClaimField; value: string | number | boolean; evidence_ids?: string[]; evidence_ref?: EvidenceRef };
export type TransactionRequest = { schemaVersion: 1; currency: string; transactions: Transaction[]; rules: TransactionRules; claims: TransactionClaim[] };
export type CheckStatus = 'consistent' | 'inconsistent' | 'review';
export type TransactionCheck = TransactionClaim & { expected: string | number | boolean | null; status: CheckStatus; reasons: string[]; required_evidence_ids?: string[]; required_evidence_ref?: EvidenceRef };
export type TransactionCase = {
  id: string; transaction: Transaction;
  signals: Array<{ rule: 'high_amount' | 'window_count'; value: string | number; threshold: string | number; unit?: string; windowMinutes?: number }>;
  window: { start: string; end: string; count: number; total: string; evidence_ref: EvidenceRef };
  claims: TransactionCheck[]; evidenceStatus: CheckStatus;
};
export type TransactionReport = {
  schemaVersion: 1; engineVersion: string; status: 'review' | 'no_signal'; currency: string; rules: TransactionRules;
  summary: { totalTransactions: number; flaggedTransactions: number; consistentClaims: number; inconsistentClaims: number; reviewClaims: number };
  evidenceIndex: Record<string, { customer_id: string; transaction_ids: string[] }>;
  cases: TransactionCase[]; claimChecks: TransactionCheck[]; limitations: string[];
  provenance: { requestSha256: string; codeSha256: Record<string, string> };
};
export type DecisionValue = 'pending' | 'escalate' | 'dismiss' | 'need_more';
export type TransactionDecision = { transaction_id: string; decision: DecisionValue; reason: string };
export type TransactionBundle = { schema: 'TransactionReviewBundleV1'; request: TransactionRequest; report: TransactionReport; decisions: TransactionDecision[]; replayed?: boolean };
export type TransactionImport = { kind: 'request'; request: TransactionRequest; ignoredColumns: string[] } | { kind: 'bundle'; bundle: TransactionBundle; sourceText: string };
export const DEFAULT_TRANSACTION_RULES: TransactionRules = { highAmount: '10000.00', windowMinutes: 10, minTransactions: 3 };

function onlyKeys(value: Record<string, unknown>, allowed: readonly string[], label: string) {
  const extra = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extra.length) throw new Error(`${label}含额外字段：${extra.join('、')}。JSON 不会静默忽略这些字段。`);
}
function reference(value: unknown): value is EvidenceRef {
  return record(value) && typeof value.group_id === 'string' && integer(value.start) && integer(value.end) && value.start <= value.end;
}
function money(value: unknown, exact?: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && typeof exact === 'string') return exact;
  throw new Error('金额须为十进制字符串或 JSON 数字，不能缺失或用布尔值替代。');
}
function requestInteger(value: unknown, exact: unknown, label: string): number {
  const token = exact === undefined && typeof value === 'number' ? String(value) : exact;
  if (typeof value !== 'number' || typeof token !== 'string' || !/^-?(?:0|[1-9]\d*)$/.test(token)) throw new Error(`${label} 须使用 JSON 整数词元，不接受小数或科学计数形式。`);
  const result = Number(token);
  if (!integer(result)) throw new Error(`${label} 须为非负安全整数，不能超出浏览器精确整数范围。`);
  return result;
}

/** A second JSON tree keeps numeric lexemes for money and strict integer input checks.
 * Strings are copied untouched; native JSON.parse remains responsible for JSON syntax. */
function numericLexemes(source: string): unknown {
  let output = ''; let quoted = false; let escaped = false; let quoteStart = 0;
  const keys: Array<Set<string> | null> = [];
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      output += char;
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') {
        quoted = false;
        let next = index + 1; while (next < source.length && /[ \t\r\n]/.test(source[next])) next += 1;
        if (source[next] === ':') {
          const name = JSON.parse(source.slice(quoteStart, index + 1)) as string;
          const object = keys.at(-1);
          if (object?.has(name)) throw new Error(`JSON 重复字段 ${name}，未接受后一个值覆盖前一个值；请删除重复键。`);
          object?.add(name);
        }
      }
    } else if (char === '"') { quoted = true; quoteStart = index; output += char; }
    else if (char === '{' || char === '[') { keys.push(char === '{' ? new Set<string>() : null); output += char; }
    else if (char === '}' || char === ']') { keys.pop(); output += char; }
    else if (char === '-' || /[0-9]/.test(char)) {
      const token = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(source.slice(index));
      if (!token) throw new Error('JSON 数字格式无效。');
      output += JSON.stringify(token[0]); index += token[0].length - 1;
    } else output += char;
  }
  return JSON.parse(output) as unknown;
}

function requestFrom(value: unknown, exact?: unknown): TransactionRequest {
  if (!record(value) || !('transactions' in value)) throw new Error('请选择完整交易请求或 TransactionReviewBundleV1 复核包；裸报告不能视为已验证结果。');
  onlyKeys(value, ['schemaVersion', 'currency', 'transactions', 'rules', 'claims'], '交易请求');
  const exactRoot = record(exact) ? exact : {};
  if (requestInteger(value.schemaVersion, exactRoot.schemaVersion, 'schemaVersion') !== 1 || typeof value.currency !== 'string' || !/^[A-Z]{3}$/.test(value.currency)) throw new Error('交易请求需要 schemaVersion: 1 和三个大写字母的单一币种。');
  if (!Array.isArray(value.transactions) || !value.transactions.length) throw new Error('transactions 必须包含至少一笔交易。');
  if (value.transactions.length > TRANSACTION_MAX_ROWS) throw new Error('交易超过 5000 行，未截断或提交；请缩小本次批次。');
  const exactRows = Array.isArray(exactRoot.transactions) ? exactRoot.transactions : [];
  const seen = new Set<string>();
  const transactions = value.transactions.map((row, index): Transaction => {
    if (!record(row)) throw new Error(`第 ${index + 1} 笔交易须为字段对象。`);
    onlyKeys(row, TRANSACTION_FIELDS, `第 ${index + 1} 笔交易`);
    for (const field of TRANSACTION_FIELDS) {
      if (!(field in row)) throw new Error(`第 ${index + 1} 笔交易缺少 ${field}。`);
      if (field !== 'amount' && (typeof row[field] !== 'string' || !row[field].trim())) throw new Error(`第 ${index + 1} 笔交易的 ${field} 必须是非空文本。`);
    }
    const id = row.transaction_id as string;
    if (seen.has(id)) throw new Error(`重复交易 ID：${id}，未跳过任何交易。`);
    seen.add(id);
    return { transaction_id: id, timestamp: row.timestamp as string, customer_id: row.customer_id as string, terminal_id: row.terminal_id as string, amount: money(row.amount, record(exactRows[index]) ? exactRows[index].amount : undefined) };
  });
  let rules = { ...DEFAULT_TRANSACTION_RULES };
  if (value.rules !== undefined) {
    if (!record(value.rules)) throw new Error('rules 须为完整规则对象。');
    onlyKeys(value.rules, ['highAmount', 'windowMinutes', 'minTransactions'], '规则');
    const exactRules = record(exactRoot.rules) ? exactRoot.rules : {};
    rules = { highAmount: money(value.rules.highAmount, exactRules.highAmount), windowMinutes: requestInteger(value.rules.windowMinutes, exactRules.windowMinutes, 'rules.windowMinutes'), minTransactions: requestInteger(value.rules.minTransactions, exactRules.minTransactions, 'rules.minTransactions') };
  }
  if (value.claims !== undefined && !Array.isArray(value.claims)) throw new Error('claims 须为结构化说明数组。');
  const exactClaims = Array.isArray(exactRoot.claims) ? exactRoot.claims : [];
  const claims = ((value.claims ?? []) as unknown[]).map((item, index): TransactionClaim => {
    if (!record(item)) throw new Error(`第 ${index + 1} 条说明不是字段对象。`);
    onlyKeys(item, ['transaction_id', 'field', 'value', 'evidence_ids', 'evidence_ref'], `第 ${index + 1} 条说明`);
    if (typeof item.transaction_id !== 'string' || !item.transaction_id.trim() || !CLAIM_FIELDS.includes(item.field as ClaimField)) throw new Error(`第 ${index + 1} 条说明的交易 ID 或字段无效。`);
    if ('evidence_ids' in item && 'evidence_ref' in item) throw new Error('一条说明不能同时提交 ID 列表和索引切片。');
    const field = item.field as ClaimField;
    const exactClaim = record(exactClaims[index]) ? exactClaims[index] : {};
    let claimValue = item.value;
    if (field === 'amount' || field === 'window_total') claimValue = money(claimValue, exactClaim.value);
    else if (field === 'window_count') claimValue = requestInteger(claimValue, exactClaim.value, `第 ${index + 1} 条说明 window_count`);
    else if (field === 'fraud_confirmed' ? typeof claimValue !== 'boolean' : typeof claimValue !== 'string') throw new Error(`第 ${index + 1} 条说明的值类型与 ${field} 不符。`);
    const base = { transaction_id: item.transaction_id, field, value: claimValue as string | number | boolean };
    if ('evidence_ref' in item) {
      if (!reference(item.evidence_ref)) throw new Error('引用索引切片需要证据组 ID、零基起点和不小于起点的终点。');
      onlyKeys(item.evidence_ref as unknown as Record<string, unknown>, ['group_id', 'start', 'end'], '索引切片');
      const exactRef = record(exactClaim.evidence_ref) ? exactClaim.evidence_ref : {};
      return { ...base, evidence_ref: { ...item.evidence_ref, start: requestInteger(item.evidence_ref.start, exactRef.start, 'evidence_ref.start'), end: requestInteger(item.evidence_ref.end, exactRef.end, 'evidence_ref.end') } };
    }
    if (item.evidence_ids !== undefined && !strings(item.evidence_ids)) throw new Error('引用交易 ID 须为文本数组。');
    return { ...base, evidence_ids: [...(item.evidence_ids as string[] | undefined ?? [])] };
  });
  const result: TransactionRequest = { schemaVersion: 1, currency: value.currency, transactions, rules, claims };
  if (bytes(JSON.stringify(result)) > TRANSACTION_MAX_BYTES) throw new Error('完整原始请求超过 2 MiB；复核包内的请求也受此限制。');
  return result;
}

function csvRows(source: string): string[][] {
  const rows: string[][] = []; let row: string[] = []; let field = ''; let quoted = false; let closed = false; let recordStarted = false;
  const endRow = () => { row.push(field); if (recordStarted) rows.push(row); row = []; field = ''; closed = false; recordStarted = false; };
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (quoted) {
      if (char === '"') { if (source[index + 1] === '"') { field += '"'; index += 1; } else { quoted = false; closed = true; } }
      else field += char;
    } else if (char === ',') { recordStarted = true; row.push(field); field = ''; closed = false; }
    else if (char === '\r' || char === '\n') { endRow(); if (char === '\r' && source[index + 1] === '\n') index += 1; }
    else if (closed) throw new Error('CSV 闭合引号后只能是逗号或换行，请检查引号格式。');
    else if (char === '"') { if (field) throw new Error('CSV 引号须从单元格开头开始。'); recordStarted = true; quoted = true; }
    else { recordStarted = true; field += char; }
  }
  if (quoted) throw new Error('CSV 引号没有闭合，未导入不完整行。');
  if (recordStarted) endRow();
  return rows;
}

export function parseTransactionInput(text: string, format?: 'csv' | 'json'): TransactionImport {
  if (READ_ONLY || STATIC_SITE) throw new Error(LOCAL);
  const size = bytes(text); const source = text.replace(/^\uFEFF/, '');
  if (size > TRANSACTION_BUNDLE_MAX_BYTES) throw new Error('复核包超过 24 MiB，未读取或截断。');
  const isJson = format === 'json' || (format !== 'csv' && /^[\s]*[\[{]/.test(source));
  if (isJson) {
    let value: unknown; let exact: unknown;
    try { value = JSON.parse(source); exact = numericLexemes(source); }
    catch (error) { if (error instanceof Error && error.message.startsWith('JSON 重复字段')) throw error; throw new Error('JSON 格式无法读取，请检查引号、逗号及完整内容。'); }
    if (record(value) && value.schema === 'TransactionReviewBundleV1') {
      requestFrom(value.request, record(exact) ? exact.request : undefined);
      if (record(value.report)) requestInteger(value.report.schemaVersion, record(exact) && record(exact.report) ? exact.report.schemaVersion : undefined, 'report.schemaVersion');
      // The uploaded report/decisions remain untrusted until /replay verifies the complete bundle.
      // Send the original complete JSON to replay, including original numeric lexemes and fields.
      return { kind: 'bundle', bundle: value as TransactionBundle, sourceText: source };
    }
    if (size > TRANSACTION_MAX_BYTES) throw new Error('原始交易请求超过 2 MiB，未导入或截断。');
    return { kind: 'request', request: requestFrom(value, exact), ignoredColumns: [] };
  }
  if (size > TRANSACTION_MAX_BYTES) throw new Error('原始 CSV 请求超过 2 MiB，未导入或截断。');
  const rows = csvRows(source);
  if (!rows.length) throw new Error('CSV 为空，请提供必需表头与交易行。');
  const headers = rows[0].map((name) => name.trim());
  if (headers.some((name) => name.toLowerCase() === 'currency')) throw new Error('CSV 的 currency 是保留币种列，不能作为普通额外列剔除。请先按币种分批，确认每批为单一币种后移除该列并在页面确认币种，或改用顶层 currency 的 JSON 请求。');
  for (const name of TRANSACTION_FIELDS) {
    const count = headers.filter((header) => header === name).length;
    if (!count) throw new Error(`CSV 缺少必需列 ${name}。`);
    if (count > 1) throw new Error(`CSV 重复必需列 ${name}。`);
  }
  if (rows.length - 1 > TRANSACTION_MAX_ROWS) throw new Error('CSV 超过 5000 行，未截断或跳过交易。');
  const transactions = rows.slice(1).map((cells, index) => {
    if (cells.length !== headers.length) throw new Error(`CSV 第 ${index + 2} 行列数为 ${cells.length}，表头为 ${headers.length}；未跳过该行。`);
    return Object.fromEntries(TRANSACTION_FIELDS.map((name) => [name, cells[headers.indexOf(name)]]));
  });
  return { kind: 'request', request: requestFrom({ schemaVersion: 1, currency: 'CNY', transactions }), ignoredColumns: [...new Set(headers.filter((name) => !TRANSACTION_FIELDS.includes(name as typeof TRANSACTION_FIELDS[number])))] };
}

export function readTransactionFile(file: File, signal: AbortSignal): Promise<string> {
  if (READ_ONLY || STATIC_SITE) return Promise.reject(new Error(LOCAL));
  if (!/\.(csv|json)$/i.test(file.name)) return Promise.reject(new Error('请选择 UTF-8 的 .csv 或 .json 文件。'));
  if (file.size > (/\.csv$/i.test(file.name) ? TRANSACTION_MAX_BYTES : TRANSACTION_BUNDLE_MAX_BYTES)) return Promise.reject(new Error(/\.csv$/i.test(file.name) ? 'CSV 超过 2 MiB。' : 'JSON 复核包超过 24 MiB。'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const clean = () => { signal.removeEventListener('abort', cancel); reader.onload = null; reader.onerror = null; reader.onabort = null; };
    const fail = (error: Error) => { clean(); reject(error); };
    const cancel = () => { clean(); if (reader.readyState === FileReader.LOADING) reader.abort(); reject(new Error('文件读取已取消。')); };
    if (signal.aborted) { cancel(); return; }
    signal.addEventListener('abort', cancel, { once: true });
    reader.onerror = () => fail(new Error('文件读取失败，请重新选择文件。'));
    reader.onabort = () => fail(new Error('文件读取已取消。'));
    reader.onload = () => {
      try {
        if (!(reader.result instanceof ArrayBuffer)) throw new Error('文件内容无法读取。');
        // Keep a leading BOM in the source-size accounting; the parser accepts and strips it.
        const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(reader.result);
        clean(); resolve(text);
      } catch { fail(new Error('文件不是有效 UTF-8，未用替换字符导入；请另存为 UTF-8。')); }
    };
    reader.readAsArrayBuffer(file);
  });
}

export function claimFromCheck(check: TransactionCheck): TransactionClaim {
  const base = { transaction_id: check.transaction_id, field: check.field, value: check.value };
  return check.evidence_ref ? { ...base, evidence_ref: { ...check.evidence_ref } } : { ...base, evidence_ids: [...(check.evidence_ids ?? [])] };
}
export function resolveTransactionEvidence(report: TransactionReport, ref: EvidenceRef): string[] {
  return report.evidenceIndex[ref.group_id].transaction_ids.slice(ref.start, ref.end);
}

function validReport(value: unknown, request: TransactionRequest): value is TransactionReport {
  if (!record(value) || value.schemaVersion !== 1 || typeof value.engineVersion !== 'string' || !['review', 'no_signal'].includes(String(value.status)) || value.currency !== request.currency
    || !record(value.rules) || typeof value.rules.highAmount !== 'string' || !integer(value.rules.windowMinutes) || !integer(value.rules.minTransactions)
    || !record(value.summary) || !['totalTransactions', 'flaggedTransactions', 'consistentClaims', 'inconsistentClaims', 'reviewClaims'].every((name) => integer((value.summary as Record<string, unknown>)[name])) || value.summary.totalTransactions !== request.transactions.length
    || !Array.isArray(value.cases) || value.summary.flaggedTransactions !== value.cases.length || !Array.isArray(value.claimChecks) || !record(value.evidenceIndex)
    || !strings(value.limitations) || !record(value.provenance) || !hash(value.provenance.requestSha256) || !record(value.provenance.codeSha256)) return false;
  if (!['backend/core/transaction_review.py', 'backend/api/transaction_review.py', 'scripts/transaction_review.py'].every((name) => hash((value.provenance as { codeSha256: Record<string, unknown> }).codeSha256[name]))) return false;
  const ids = new Set(request.transactions.map((row) => row.transaction_id));
  const groups = value.evidenceIndex;
  if (!Object.values(groups).every((group) => record(group) && typeof group.customer_id === 'string' && strings(group.transaction_ids) && group.transaction_ids.every((id) => ids.has(id)))) return false;
  const validRef = (ref: unknown) => {
    if (!reference(ref)) return false;
    const group = groups[ref.group_id];
    return record(group) && Array.isArray(group.transaction_ids) && ref.end <= group.transaction_ids.length;
  };
  const validCheck = (check: unknown) => record(check) && typeof check.transaction_id === 'string' && CLAIM_FIELDS.includes(check.field as ClaimField)
    && ['string', 'number', 'boolean'].includes(typeof check.value) && ['consistent', 'inconsistent', 'review'].includes(String(check.status)) && strings(check.reasons)
    && (check.evidence_ids === undefined || strings(check.evidence_ids)) && (check.evidence_ref === undefined || validRef(check.evidence_ref))
    && (check.required_evidence_ids === undefined || strings(check.required_evidence_ids)) && (check.required_evidence_ref === undefined || validRef(check.required_evidence_ref));
  return value.claimChecks.every(validCheck) && value.cases.every((item) => {
    if (!record(item) || !record(item.transaction)) return false;
    const transaction = item.transaction;
    return typeof item.id === 'string' && ids.has(item.id)
      && TRANSACTION_FIELDS.every((field) => typeof transaction[field] === 'string') && transaction.transaction_id === item.id
      && ['consistent', 'inconsistent', 'review'].includes(String(item.evidenceStatus)) && Array.isArray(item.claims) && item.claims.every(validCheck)
      && Array.isArray(item.signals) && item.signals.every((signal) => record(signal) && ['high_amount', 'window_count'].includes(String(signal.rule)) && ['number', 'string'].includes(typeof signal.value) && ['number', 'string'].includes(typeof signal.threshold))
      && record(item.window) && typeof item.window.start === 'string' && typeof item.window.end === 'string' && integer(item.window.count) && typeof item.window.total === 'string' && validRef(item.window.evidence_ref);
  });
}

export function validateTransactionDecisions(decisions: TransactionDecision[], report: TransactionReport): void {
  if (!Array.isArray(decisions)) throw new Error('人工决定必须是记录数组。');
  const ids = new Set(report.cases.map((item) => item.id)); const seen = new Set<string>();
  for (const item of decisions) {
    if (!record(item) || !ids.has(item.transaction_id) || seen.has(item.transaction_id) || !['pending', 'escalate', 'dismiss', 'need_more'].includes(item.decision) || typeof item.reason !== 'string') throw new Error('人工决定含未知/重复案件或无效决定，请重新核对。');
    if (item.decision !== 'pending' && !item.reason.trim()) throw new Error(`案件 ${item.transaction_id} 的人工决定必须填写非空原因，不能导出空原因。`);
    seen.add(item.transaction_id);
  }
}

async function postTransaction(path: 'analyze' | 'replay', body: string, signal?: AbortSignal): Promise<unknown> {
  if (READ_ONLY || STATIC_SITE) throw new Error(LOCAL);
  const limit = path === 'analyze' ? TRANSACTION_MAX_BYTES : TRANSACTION_BUNDLE_MAX_BYTES;
  if (bytes(body) > limit) throw new Error(path === 'analyze' ? '完整原始请求超过 2 MiB。' : '完整复核包超过 24 MiB，未截断或提交。');
  const controller = new AbortController(); const cancel = () => controller.abort();
  if (signal?.aborted) throw new Error('本次请求已取消。');
  signal?.addEventListener('abort', cancel, { once: true });
  const timeout = window.setTimeout(cancel, 30000);
  try {
    const response = await fetch(`/api/transaction-review/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...sessionHeaders() }, body, signal: controller.signal });
    if (controller.signal.aborted) throw new Error('本次请求已取消或等待超时，请检查本地服务后显式重试。');
    if (!response.ok) {
      let detail: unknown;
      try { const error: unknown = await response.json(); detail = record(error) ? error.detail : null; } catch { detail = null; }
      throw new Error(typeof detail === 'string' && detail.length <= 1000 ? detail : response.status === 423 ? LOCAL : response.status === 413 ? '请求超过服务大小限制，未执行核对。' : '本地服务未完成交易复核，请检查输入后重试。');
    }
    let output: unknown;
    try { output = await response.json(); } catch { throw new Error('本地服务返回的内容不是有效 JSON，未采用该结果。'); }
    if (controller.signal.aborted) throw new Error('本次请求已取消或等待超时。');
    return output;
  } catch (error) {
    if (controller.signal.aborted) throw new Error('本次请求已取消或等待超时，请检查本地服务后显式重试。');
    if (error instanceof TypeError) throw new Error('本地交易复核服务连接失败，请检查服务连接后重试。');
    throw error;
  } finally { window.clearTimeout(timeout); signal?.removeEventListener('abort', cancel); }
}

export async function analyzeTransactions(request: TransactionRequest, signal?: AbortSignal): Promise<TransactionBundle> {
  const input = requestFrom(request);
  const output = await postTransaction('analyze', JSON.stringify(input), signal);
  if (!validReport(output, input)) throw new Error('服务报告结构、证据引用或请求范围不一致，未采用该报告。');
  return { schema: 'TransactionReviewBundleV1', request: input, report: output, decisions: [] };
}
export async function replayTransactions(bundle: TransactionBundle, signal?: AbortSignal, sourceText?: string): Promise<TransactionBundle> {
  if (sourceText === undefined) requestFrom(bundle.request);
  const output = await postTransaction('replay', sourceText ?? JSON.stringify(bundle), signal);
  if (!record(output) || output.schema !== 'TransactionReviewBundleV1' || output.replayed !== true) throw new Error('服务未返回成功重开标记，未采用上传报告。');
  const request = requestFrom(output.request);
  if (!validReport(output.report, request)) throw new Error('重开返回报告的结构或证据引用无效，未采用上传报告。');
  validateTransactionDecisions(output.decisions as TransactionDecision[], output.report);
  return { schema: 'TransactionReviewBundleV1', request, report: output.report, decisions: output.decisions as TransactionDecision[], replayed: true };
}

export function downloadTransactionBundle(bundle: TransactionBundle): void {
  validateTransactionDecisions(bundle.decisions, bundle.report);
  const text = JSON.stringify({ schema: bundle.schema, request: bundle.request, report: bundle.report, decisions: bundle.decisions });
  if (bytes(text) > TRANSACTION_BUNDLE_MAX_BYTES) throw new Error('完整复核包超过 24 MiB，无法保证重开；未截断或下载，请缩小输入批次重新分析。');
  let url: string | undefined; let anchor: HTMLAnchorElement | undefined;
  try {
    url = URL.createObjectURL(new Blob([text], { type: 'application/json;charset=utf-8' }));
    anchor = document.createElement('a'); anchor.href = url; anchor.download = 'transaction-review-bundle.json';
    document.body.appendChild(anchor); anchor.click();
  } catch { throw new Error('下载未完成，请检查浏览器下载设置后重试。'); }
  finally { anchor?.remove(); if (url) { const held = url; window.setTimeout(() => URL.revokeObjectURL(held), 1000); } }
}
