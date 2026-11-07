import { READ_ONLY, STATIC_SITE } from '../config';
import { readFileBytes } from './parentContext';
import { sessionHeaders } from './session';

export const FINANCE_MAX_BYTES = 1024 * 1024;
const LOCAL_MESSAGE = '请在本地服务中核对资料；公开演示和静态页面不接收文件。';
const BINDING_MESSAGE = '报告结构或本次输入绑定不一致，未采用该报告。';
const CODE_PATHS = ['scripts/statute_diagnostic.py', 'backend/core/statute_check.py', 'backend/core/statute_locate.py', 'backend/api/finance_review.py'];
export type FinanceRequest = {
  schemaVersion: 1; sourceFormat: 'article-lines-v1'; source: string; answer: string;
  contexts: string[]; question: string; engine: 'base' | 'extended';
};
export type FinanceFinding = { code: string; severity: 'error' | 'warn' | 'info'; title: string; text: string; detail: string; cited?: string; located?: string };
type BaseResult = { flagged: boolean; findings: FinanceFinding[]; contextProvisions: string[][] };
type ExtendedResult = { flagged: boolean; original: BaseResult; extensionFindings: FinanceFinding[]; recoveries: Array<{ recovered: string | null }> };
export type FinanceReport = {
  schemaVersion: 1; status: 'invalid_input' | 'review' | 'findings' | 'no_error_found'; flagged: boolean | null;
  engine: 'base' | 'extended' | null; diagnostics: Array<{ code: string; message: string; line?: number }>;
  sourceAudit: { nonblankLines: number; parsedLines: number; unparsedLines: number[]; articleCount: number } | null;
  result: BaseResult | ExtendedResult | null;
  structureCheck: { status: 'not_provided' | 'matched' | 'mismatch'; referenceSha256: string | null; expectedUnits: number | null; observedUnits: number; scope: string } | null;
  provenance: { inputSha256: string; codeSha256: Record<string, string>; pythonVersion: string };
  limitations: string[];
};
export type FinanceBundle = { schema: 'FinanceReviewBundleV1'; requestText: string; report: FinanceReport };

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const hash = (value: unknown): value is string => text(value) && /^[a-f0-9]{64}$/.test(value);
const finding = (value: unknown): value is FinanceFinding => record(value) && text(value.code) && ['error', 'warn', 'info'].includes(String(value.severity)) && text(value.title) && text(value.text) && text(value.detail);
const baseResult = (value: unknown): value is BaseResult => record(value) && typeof value.flagged === 'boolean' && Array.isArray(value.findings) && value.findings.every(finding) && Array.isArray(value.contextProvisions) && value.contextProvisions.every((row) => Array.isArray(row) && row.every(text));
function report(value: unknown): value is FinanceReport {
  if (!record(value) || value.schemaVersion !== 1 || !['invalid_input', 'review', 'findings', 'no_error_found'].includes(String(value.status))
    || !['base', 'extended', null].includes(value.engine as 'base' | 'extended' | null)
    || !Array.isArray(value.diagnostics) || !value.diagnostics.every((item) => record(item) && text(item.code) && text(item.message))
    || !Array.isArray(value.limitations) || !value.limitations.every(text)
    || !record(value.provenance) || !hash(value.provenance.inputSha256) || !text(value.provenance.pythonVersion) || !record(value.provenance.codeSha256)) return false;
  const codeHashes = value.provenance.codeSha256;
  if (!CODE_PATHS.every((name) => hash(codeHashes[name]))) return false;
  if (value.structureCheck !== null && (!record(value.structureCheck) || !['not_provided', 'matched', 'mismatch'].includes(String(value.structureCheck.status)))) return false;
  if (value.status === 'review' || value.status === 'invalid_input') return value.flagged === null && value.result === null && value.diagnostics.length > 0;
  if (value.flagged !== (value.status === 'findings') || !record(value.result) || value.result.flagged !== value.flagged) return false;
  return value.engine === 'base' ? baseResult(value.result)
    : baseResult(value.result.original) && Array.isArray(value.result.extensionFindings) && value.result.extensionFindings.every(finding)
      && Array.isArray(value.result.recoveries) && value.result.recoveries.every((item) => record(item) && (item.recovered === null || text(item.recovered)));
}

export function financeFindings(value: FinanceReport): FinanceFinding[] {
  if (!value.result) return [];
  return 'original' in value.result ? [...value.result.original.findings, ...value.result.extensionFindings] : value.result.findings;
}

export async function readFinanceFile(file: File): Promise<string> {
  if (READ_ONLY || STATIC_SITE) throw new Error(LOCAL_MESSAGE);
  if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 UTF-8 编码的 .txt 文件。');
  if (file.size > FINANCE_MAX_BYTES) throw new Error('TXT 超过 1 MiB，请缩小本次资料范围。');
  const bytes = await readFileBytes(file);
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { throw new Error('文件不是有效 UTF-8，未用替换字符导入。请另存为 UTF-8 TXT。'); }
}

/** Preserve the exact UTF-8 JSON sent on the wire for download and CLI replay. */
export async function checkFinanceReview(input: FinanceRequest, signal?: AbortSignal): Promise<FinanceBundle> {
  if (READ_ONLY || STATIC_SITE) throw new Error(LOCAL_MESSAGE);
  const requestText = JSON.stringify(input);
  const bytes = new TextEncoder().encode(requestText);
  if (bytes.length > FINANCE_MAX_BYTES) throw new Error('完整请求超过 1 MiB，原文与核对资料均计入大小。请缩小资料范围。');
  if (!globalThis.crypto?.subtle) throw new Error('请通过本机 localhost、127.0.0.1 或 HTTPS 打开页面以核对输入哈希。');
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) throw new Error('本次核对已取消。');
  signal?.addEventListener('abort', cancel, { once: true });
  const timeout = window.setTimeout(cancel, 30000);
  try {
    const response = await fetch('/api/finance-review', { method: 'POST', headers: { 'Content-Type': 'application/json', ...sessionHeaders() }, body: requestText, signal: controller.signal });
    if (controller.signal.aborted) throw new Error('本次核对已取消或等待超时。');
    if (!response.ok) {
      const fallback = response.status === 423 ? LOCAL_MESSAGE : response.status === 413 ? '完整请求超过 1 MiB，未执行核对。' : '本地服务未完成核对，请检查输入和服务后重试。';
      let detail: unknown;
      try { const body: unknown = await response.json(); detail = record(body) ? body.detail : null; } catch { detail = null; }
      throw new Error(text(detail) && detail.length <= 500 ? detail : fallback);
    }
    const output: unknown = await response.json();
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const inputHash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
    if (controller.signal.aborted) throw new Error('本次核对已取消或等待超时。');
    if (!report(output) || output.provenance.inputSha256 !== inputHash) throw new Error(BINDING_MESSAGE);
    return { schema: 'FinanceReviewBundleV1', requestText, report: output };
  } catch (error) {
    if (controller.signal.aborted) throw new Error('本次核对已取消或等待超时。');
    if (error instanceof TypeError) throw new Error('未取得核对结果，请检查本地服务连接后重试。');
    throw error;
  } finally { window.clearTimeout(timeout); signal?.removeEventListener('abort', cancel); }
}

export function downloadFinanceReview(bundle: FinanceBundle, format: 'bundle' | 'request'): void {
  const content = format === 'request' ? bundle.requestText : JSON.stringify(bundle, null, 2);
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = format === 'request' ? 'finance-review-request.json' : 'finance-review-report.json';
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
