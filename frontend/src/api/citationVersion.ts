import { READ_ONLY, STATIC_SITE } from '../config';
import { readFileBytes } from './parentContext';

export const CV_MAX_BYTES = 5 * 1024 * 1024;
export const CV_MAX_FILES = 10;
export const CV_MAX_CITATIONS = 20;
export const CV_TIMEOUT_MS = 30000;
export const CV_PUBLIC_MESSAGE = '公开演示不接收 XML 文件，请在本地运行后使用引用版本检查。';
export const CV_HASH_NOTE = 'SHA-256 只绑定原始字节；不证明语义正确，也不证明模型输出可复现。';
export const CV_HASH_COMMAND = 'Get-FileHash -Algorithm SHA256 -LiteralPath .\\your-source.xml';
export const SUBCHECKS = ['identity', 'view', 'snapshot_date', 'language', 'extent', 'fragment_interval', 'conditions'] as const;
export const AXES = ['snapshot_binding', 'fragment_metadata', 'known_by'] as const;
export type CVState = 'MATCH' | 'MISMATCH' | 'REVIEW' | 'INVALID_INPUT';
export type Evidence = { path: string; raw: string | null };
export type CVCheck = { state: CVState; reason: string; evidence: Evidence[] };
export type CVFile = { id: string; name: string; base64: string };
export type LocalCVFile = CVFile & { sha256: string; size_bytes: number };
export type Provision = { provision_id: string | null; view_uri: string | null; snapshot_date: string | null; extent: string | null; start: string | null; end: string | null };
export type InspectedFile = { id: string; name: string; sha256: string | null; size_bytes: number | null; status: 'READY' | 'INVALID_INPUT'; error: string | null; observed: { title: string | null; document_id: string | null; language: string | null; provisions: Provision[]; ambiguities: string[] } | null };
export type CVInspection = { schema: 'CitationVersionInspectionV1'; files: InspectedFile[] };
export type CVRequest = { source_sha256: string; document_id: string; provision_id: string; view_uri: string; snapshot_date: string; check_date: string; extent: string; language: string };
export type CVCitation = { id: string; label: string; file_id: string; request: CVRequest };
export type CVInput = { schema: 'CitationVersionCheckV1'; files: CVFile[]; citations: CVCitation[] };
export type CVOutput = {
  method: 'B1'; source_sha256: string | null;
  subchecks: Record<typeof SUBCHECKS[number], { state: CVState; checks: CVCheck[] }>;
  axes: Record<typeof AXES[number], CVState>;
  target_location: CVCheck | null; owners: Record<string, string | null>;
  date_roles: Record<string, { role: string; values: Evidence[] | null }>; input_errors: string[];
};
export type CVRow = { id: string; label: string; file_id: string; output: CVOutput };
export type CVReport = {
  schema: 'CitationVersionReportV1'; checker: { version: string; method: 'B1'; source_sha256: { rules: string; wrapper: string } };
  inputs: { files: Array<{ id: string; name: string; sha256: string | null; size_bytes: number | null }>; citations: CVCitation[] };
  rows: CVRow[]; counts: { total: number; valid_input: number; conflicts: number; field_review: number; known_by_review: number; invalid: number };
  count_semantics: Record<string, string>; limitations: string[];
};
export type CVBundle = { schema: 'CitationVersionBundleV1'; input: CVInput; report: CVReport };

export const STATE_LABELS: Record<CVState, string> = { MATCH: '字段相符', MISMATCH: '存在冲突', REVIEW: '待复核', INVALID_INPUT: '输入无效' };
export const CHECK_LABELS: Record<typeof SUBCHECKS[number], string> = { identity: '文档与条款身份', view: '视图 URI', snapshot_date: '视图日期', language: '语言', extent: '地域', fragment_interval: '片段日期区间', conditions: '附加条件' };
export const AXIS_LABELS: Record<typeof AXES[number], string> = { snapshot_binding: '快照绑定', fragment_metadata: '片段元数据', known_by: '当时已知信息（known_by）' };
export const KNOWN_BY_NOTE = 'known_by 始终保留复核：仅凭当前 XML 不能确定当时已知的信息，需另查公布或归档记录。';
/** A short visible overview never replaces the complete checks and field records below it. */
export function subcheckOverview(part: CVOutput['subchecks'][typeof SUBCHECKS[number]]) {
  const first = part.checks.find((item) => item.state === part.state) ?? part.checks[0];
  return {
    reasonLabel: first ? `${STATE_LABELS[first.state]}原因（首条）` : '输入提示',
    reason: first?.reason ?? '本项未产生逐项原因，请查看输入问题。',
    counts: `${part.checks.length} 条检查 · ${part.checks.reduce((sum, item) => sum + item.evidence.length, 0)} 项原字段记录`,
  };
}
export function nextAction(key: typeof SUBCHECKS[number], state: CVState): string {
  if (state === 'MATCH') return '此项仅表示字段相符；继续核对其余分项与 known_by。';
  if (state === 'INVALID_INPUT') return '核对请求格式与所选文件；补齐或更换输入后重新检查。';
  const actions = {
    identity: '查看文档与条款标识，确认资料对应本次引用；必要时重新选择文件或条款。',
    view: '对照原件 URI 和所需视图 URI；核对你的版本需求，或换用该视图的资料。',
    snapshot_date: '对照原件日期和所需视图日期；核对需求，或换用相应日期的资料。',
    language: '核对所需语言与原件语言；若需求无误，请更换语言版本。',
    extent: '查看地域字段；确认所需地域，或查找覆盖该地域的资料。',
    fragment_interval: '查看开始、结束日期及区间规则，核对片段查询日期；必要时查找其他时间的资料。',
    conditions: '逐项查看条件字段与限定；无法确认时继续保留复核。',
  };
  return actions[key];
}

export class CVError extends Error {
  constructor(message: string, public readonly code: string) { super(message); this.name = 'CitationVersionError'; }
}
const MALFORMED = '本地服务返回的报告结构或文件绑定不符合约定，未采用。请确认前后端版本一致后重试。';
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const nullableText = (value: unknown) => value === null || text(value);
const texts = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const hash = (value: unknown) => text(value) && /^[a-f0-9]{64}$/.test(value);
const count = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
const state = (value: unknown): value is CVState => text(value) && ['MATCH', 'MISMATCH', 'REVIEW', 'INVALID_INPUT'].includes(value);
const evidence = (value: unknown): value is Evidence[] => Array.isArray(value) && value.every((item) => record(item) && text(item.path) && nullableText(item.raw));
const check = (value: unknown): value is CVCheck => record(value) && state(value.state) && text(value.reason) && evidence(value.evidence);
const canonical = (value: unknown): string => JSON.stringify(value, (_key, item: unknown) => record(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
function output(value: unknown): value is CVOutput {
  return record(value) && value.method === 'B1' && (value.source_sha256 === null || hash(value.source_sha256))
    && record(value.axes) && AXES.every((key) => state((value.axes as Record<string, unknown>)[key]))
    && record(value.subchecks) && SUBCHECKS.every((key) => { const item = (value.subchecks as Record<string, unknown>)[key]; return record(item) && state(item.state) && Array.isArray(item.checks) && item.checks.every(check); })
    && (value.target_location === null || check(value.target_location)) && record(value.owners) && Object.values(value.owners).every(nullableText)
    && record(value.date_roles) && Object.values(value.date_roles).every((item) => record(item) && text(item.role) && (item.values === null || evidence(item.values))) && texts(value.input_errors);
}

/** Read original bytes. In public builds this guard runs before FileReader or Web Crypto. */
export async function loadCitationFile(file: File, id: string): Promise<LocalCVFile> {
  if (READ_ONLY || STATIC_SITE) throw new CVError(CV_PUBLIC_MESSAGE, 'READ_ONLY');
  if (file.size > CV_MAX_BYTES) throw new CVError('单份 XML 不能超过 5 MiB。', 'SIZE');
  const bytes = await readFileBytes(file);
  if (bytes.length > CV_MAX_BYTES) throw new CVError('读取后的 XML 超过 5 MiB，请重新选择。', 'SIZE');
  if (!globalThis.crypto?.subtle) throw new CVError('此浏览器无法计算文件哈希，请通过本机 localhost 或 127.0.0.1 地址打开页面。', 'CRYPTO');
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return { id, name: file.name, base64: btoa(binary), sha256, size_bytes: bytes.length };
}

const wireFiles = (files: LocalCVFile[]): CVFile[] => files.map(({ id, name, base64 }) => ({ id, name, base64 }));
export const makeCVInput = (files: LocalCVFile[], citations: CVCitation[]): CVInput => ({ schema: 'CitationVersionCheckV1', files: wireFiles(files), citations });

/** One bounded request, never retried automatically. Caller cancellation, timeout and late replies stay distinct. */
async function post(path: string, body: unknown, signal?: AbortSignal): Promise<unknown> {
  if (READ_ONLY || STATIC_SITE) throw new CVError(CV_PUBLIC_MESSAGE, 'READ_ONLY');
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (signal?.aborted) throw new CVError('本次操作已取消。', 'CANCELLED');
  signal?.addEventListener('abort', cancel, { once: true });
  const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, CV_TIMEOUT_MS);
  try {
    const response = await fetch(`/api/citations/versions/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal });
    if (controller.signal.aborted) throw new Error('aborted');
    if (!response.ok) {
      let detail: unknown;
      try { const data: unknown = await response.json(); detail = record(data) ? data.detail : null; } catch { detail = null; }
      const fallback = response.status === 423 ? CV_PUBLIC_MESSAGE : response.status === 404 ? '本地后端尚未提供引用版本检查，请启动对应版本的服务。' : response.status === 413 ? '文件合计不能超过 5 MiB。' : '本地服务未完成检查，请检查输入或本地服务后重试。';
      throw new CVError(text(detail) && detail.length > 0 && detail.length <= 500 ? detail : fallback, `HTTP_${response.status}`);
    }
    const result: unknown = await response.json();
    if (controller.signal.aborted) throw new Error('aborted');
    return result;
  } catch (error) {
    if (timedOut) throw new CVError('本地服务在 30 秒内未返回，已停止等待。文件和需求仍保留，可检查服务后重试。', 'TIMEOUT');
    if (signal?.aborted) throw new CVError('本次操作已取消。', 'CANCELLED');
    if (error instanceof CVError) throw error;
    throw new CVError('无法取得完整结果。请检查本地服务连接后重试；文件和需求仍保留。', 'CONNECTION');
  } finally { window.clearTimeout(timeout); signal?.removeEventListener('abort', cancel); }
}

export async function inspectCitationFiles(files: LocalCVFile[], signal?: AbortSignal): Promise<CVInspection> {
  const data = await post('inspect', { schema: 'CitationVersionFilesV1', files: wireFiles(files) }, signal);
  if (!record(data) || data.schema !== 'CitationVersionInspectionV1' || !Array.isArray(data.files) || data.files.length !== files.length) throw new CVError(MALFORMED, 'RESPONSE');
  const valid = data.files.every((item, index) => {
    const original = files[index];
    if (!record(item) || item.id !== original.id || item.name !== original.name || item.sha256 !== original.sha256 || item.size_bytes !== original.size_bytes || !['READY', 'INVALID_INPUT'].includes(String(item.status)) || !nullableText(item.error)) return false;
    if (item.observed === null) return item.status === 'INVALID_INPUT';
    const observed = item.observed;
    return record(observed) && nullableText(observed.title) && nullableText(observed.document_id) && nullableText(observed.language) && texts(observed.ambiguities)
      && Array.isArray(observed.provisions) && observed.provisions.every((part) => record(part) && ['provision_id', 'view_uri', 'snapshot_date', 'extent', 'start', 'end'].every((key) => nullableText(part[key])));
  });
  if (!valid) throw new CVError(MALFORMED, 'RESPONSE');
  return data as unknown as CVInspection;
}

/** Reject reordered, dropped or rebound rows; a saved report must refer to exactly the submitted request. */
export async function checkCitationVersions(input: CVInput, files: LocalCVFile[], signal?: AbortSignal): Promise<CVBundle> {
  const data = await post('check', input, signal);
  const fileById = new Map(files.map((file) => [file.id, file]));
  if (!record(data) || data.schema !== 'CitationVersionReportV1' || !record(data.checker) || !text(data.checker.version) || data.checker.method !== 'B1' || !record(data.checker.source_sha256) || !hash(data.checker.source_sha256.rules) || !hash(data.checker.source_sha256.wrapper)
    || !record(data.inputs) || canonical(data.inputs.citations) !== canonical(input.citations) || !Array.isArray(data.inputs.files) || data.inputs.files.length !== files.length
    || !data.inputs.files.every((file, i) => record(file) && file.id === files[i].id && file.name === files[i].name && file.sha256 === files[i].sha256 && file.size_bytes === files[i].size_bytes)
    || !Array.isArray(data.rows) || data.rows.length !== input.citations.length || !data.rows.every((row, i) => record(row) && row.id === input.citations[i].id && row.label === input.citations[i].label && row.file_id === input.citations[i].file_id && output(row.output) && (row.output.input_errors.length > 0 || (row.output.source_sha256 === fileById.get(String(row.file_id))?.sha256 && row.output.axes.known_by === 'REVIEW')))
    || !record(data.counts) || !['total', 'valid_input', 'conflicts', 'field_review', 'known_by_review', 'invalid'].every((key) => count((data.counts as Record<string, unknown>)[key]))
    || data.counts.total !== input.citations.length || Number(data.counts.valid_input) + Number(data.counts.invalid) !== input.citations.length
    || ['conflicts', 'field_review', 'known_by_review'].some((key) => Number((data.counts as Record<string, unknown>)[key]) > Number((data.counts as Record<string, unknown>).valid_input))
    || !record(data.count_semantics) || !Object.values(data.count_semantics).every(text) || !texts(data.limitations)) throw new CVError(MALFORMED, 'RESPONSE');
  const report = data as unknown as CVReport;
  const validRows = report.rows.filter((row) => !AXES.some((key) => row.output.axes[key] === 'INVALID_INPUT'));
  if (report.counts.valid_input !== validRows.length || report.counts.invalid !== report.rows.length - validRows.length
    || report.counts.conflicts !== validRows.filter((row) => SUBCHECKS.some((key) => row.output.subchecks[key].state === 'MISMATCH')).length
    || report.counts.field_review !== validRows.filter((row) => SUBCHECKS.some((key) => row.output.subchecks[key].state === 'REVIEW')).length
    || report.counts.known_by_review !== validRows.filter((row) => row.output.axes.known_by === 'REVIEW').length) throw new CVError(MALFORMED, 'COUNTS');
  return { schema: 'CitationVersionBundleV1', input, report };
}

/** Only an explicit user action invokes this helper. No source date is adopted as the desired date. */
export function suggestedViewUri(observed: string | null | undefined, desiredDate: string): string | null {
  if (!observed || !/^\d{4}-\d{2}-\d{2}$/.test(desiredDate) || !/^https?:\/\/www\.legislation\.gov\.uk\/[^?#%\\\s]+\/\d{4}-\d{2}-\d{2}$/.test(observed)) return null;
  return observed.replace(/\d{4}-\d{2}-\d{2}$/, desiredDate);
}

const escapeHtml = (value: unknown): string => String(value ?? '未提供').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]!));
const evidenceHtml = (items: Evidence[]) => items.length ? `<dl>${items.map((item) => `<dt><code>${escapeHtml(item.path)}</code></dt><dd><code>${escapeHtml(item.raw)}</code></dd>`).join('')}</dl>` : '<p>没有可用字段证据。</p>';

/** Portable, script-free report. Even filenames, reasons, JSON and field paths are escaped as plain text. */
export function citationReportHtml(bundle: CVBundle, historical = false): string {
  const { report } = bundle;
  const filesHtml = report.inputs.files.map((file) => `<h3>${escapeHtml(file.name)} · ${escapeHtml(file.id)}</h3><p>${escapeHtml(file.size_bytes)} 字节</p><p>原始字节 SHA-256：<code>${escapeHtml(file.sha256)}</code></p>`).join('');
  const rowsHtml = report.rows.map((row, index) => {
    const checksHtml = SUBCHECKS.map((key) => {
      const part = row.output.subchecks[key];
      const overview = subcheckOverview(part);
      const completeChecks = part.checks.map((item) => `<div class="check-record"><p>${escapeHtml(item.reason)}（${STATE_LABELS[item.state]}）</p>${evidenceHtml(item.evidence)}</div>`).join('');
      return `<div class="check" data-check="${key}" data-state="${part.state}">
        <h3>${CHECK_LABELS[key]} · <span class="${part.state}">${STATE_LABELS[part.state]} · ${part.state}</span></h3>
        <p class="reason">${escapeHtml(overview.reasonLabel)}：${escapeHtml(overview.reason)}</p><p>${escapeHtml(overview.counts)}</p>
        <p>下一步：${escapeHtml(nextAction(key, part.state))}</p>
        <details class="check-evidence"${part.state === 'MISMATCH' ? ' open' : ''}><summary>查看完整原字段与逐项原因</summary>
          <div class="evidence-scroll" role="region" aria-label="引用条目 ${index + 1} ${CHECK_LABELS[key]}完整依据" tabindex="0">${completeChecks || '<p>没有逐项依据；输入问题保留在上方。</p>'}</div>
        </details>
      </div>`;
    }).join('');
    return `<section><h2>${index + 1}. ${escapeHtml(row.label || `条目 ${index + 1}`)}</h2><p>条目 ID：${escapeHtml(row.id)}；文件 ID：${escapeHtml(row.file_id)}</p>
      <h3>本次用户要求</h3><pre>${escapeHtml(JSON.stringify(bundle.input.citations[index].request, null, 2))}</pre>
      <h3>三条判断轴</h3><ul>${AXES.map((key) => `<li>${escapeHtml(AXIS_LABELS[key])}：<strong class="${row.output.axes[key]}">${STATE_LABELS[row.output.axes[key]]} · ${row.output.axes[key]}</strong></li>`).join('')}</ul>
      ${row.output.input_errors.length ? `<h3>输入问题</h3><ul>${row.output.input_errors.map((error) => `<li>${escapeHtml(error)}</li>`).join('')}</ul>` : ''}${checksHtml}
      <details><summary>完整原始输出（含定位、日期角色与归属）</summary><pre class="raw-output" data-raw-output="${index}" tabindex="0" role="region" aria-label="引用条目 ${index + 1} 完整原始输出">${escapeHtml(JSON.stringify(row.output, null, 2))}</pre></details>
    </section>`;
  }).join('');
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>引用版本检查${historical ? ' · 上次报告' : ''}</title><style>
    body{font:16px/1.65 system-ui,sans-serif;color:#17212c;margin:32px auto;padding:0 20px;max-width:1000px}
    h1,h2,h3{line-height:1.4}section{border-top:1px solid #acb5be;margin-top:30px;padding-top:18px}
    code,pre{overflow-wrap:anywhere;white-space:pre-wrap}dt{font-weight:600}dd{margin:0 0 12px}li{margin:6px 0}
    .MISMATCH,.INVALID_INPUT{color:#a02020}.REVIEW{color:#795000}.MATCH{color:#175c50}
    table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #ced4d9;padding:8px;vertical-align:top}p,td,th{overflow-wrap:anywhere}
    .check{padding:12px 0;border-top:1px solid #ced4d9}.check h3{margin:4px 0 10px}.check>p{margin:6px 0}
    summary{cursor:pointer;padding:6px 0}.evidence-scroll,.raw-output{box-sizing:border-box;max-height:400px;overflow:auto;overscroll-behavior:contain;margin:10px 0;padding:12px;border:1px solid #acb5be;border-radius:6px;scrollbar-width:thin}
    .evidence-scroll:focus-visible,.raw-output:focus-visible,summary:focus-visible{outline:2px solid #8b630e;outline-offset:3px}.check-record+.check-record{border-top:1px solid #ced4d9;padding-top:8px}
    @media print{body{margin:0;max-width:none}.evidence-scroll,.raw-output{max-height:none;overflow:visible}}
  </style></head><body><h1>引用版本检查${historical ? ' · 上次报告' : ''}</h1>
    ${historical ? '<p><strong>历史报告，仅对应本包保存的原始文件与请求，不代表当前页面输入。</strong></p>' : ''}
    <p>英国官方 CLML XML 版本字段核对；不判断引文语义或法律适用。文档身份来自所选文件，须自行确认资料与引用对应。没有总通过结论。</p><p>${escapeHtml(KNOWN_BY_NOTE)}</p>
    <p>条目 ${report.counts.total}；有效输入 ${report.counts.valid_input}；输入无效 ${report.counts.invalid}；冲突 ${report.counts.conflicts}；字段待复核 ${report.counts.field_review}；known_by 待复核 ${report.counts.known_by_review}。后三项可重叠，不求和。</p>
    <section><h2>原始文件绑定</h2>${filesHtml}<details><summary>核对原始文件哈希</summary><p>${escapeHtml(CV_HASH_NOTE)}</p><pre>${escapeHtml(CV_HASH_COMMAND)}</pre><p>在 PowerShell 中将 your-source.xml 换成原始 XML 文件路径后运行。</p></details></section>
    ${rowsHtml}
    <section><h2>范围与复核</h2><ul>${report.limitations.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul><p>自包含 JSON 复核包保存原始文件字节、请求与报告，可供离线重新执行比较。HTML 本身不是独立复算结果。</p><details><summary>检查器与计数口径</summary><pre>${escapeHtml(JSON.stringify({ checker: report.checker, count_semantics: report.count_semantics }, null, 2))}</pre></details></section>
  </body></html>`;
}

export function downloadCitationReport(bundle: CVBundle, format: 'html' | 'json', historical = false): void {
  const body = format === 'html' ? citationReportHtml(bundle, historical) : `${JSON.stringify(bundle, null, 2)}\n`;
  const url = URL.createObjectURL(new Blob([body], { type: format === 'html' ? 'text/html;charset=utf-8' : 'application/json;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `citation-version-${historical ? 'previous-' : ''}${format === 'html' ? 'report.html' : 'bundle.json'}`;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
