export type KUMode = 'scan' | 'recheck';
export type KUSide = 'old' | 'new';
export type KUSource = {
  source_id: string; title: string; url: string; collector_ready: boolean;
  extraction: { fetch_backend: string; include_filters: string[] };
};
export type KUVersion = { timestamp: string; captured_at: string };
export type KUSnapshot = KUVersion & { source_id: string; sha256: string; size: number; text: string };
/** Empty selection, explicit absence and unread text are distinct states. */
export type KUSnapshotInput = { version: string; text: string | null; snapshot?: KUSnapshot };
export type KUSourceInput = { source: KUSource; old: KUSnapshotInput; new: KUSnapshotInput };
export type KUCitation = { source_id: string; quote: string; line_start: number; line_end: number };
export type KUItemInput = {
  key: number; item_id: string; question: string; old_draft: string;
  source_ids: string[]; old_citations: KUCitation[];
  revision: { enabled: boolean; proposed_draft: string; citations: KUCitation[]; open_questions: string };
};
export type KUInput = { task: string; files: Record<string, string>; revisions?: string };
export type KUReport = {
  schema_version: string; mode: KUMode; run_status: string;
  summary: Record<string, unknown>; bindings: Record<string, unknown>;
  sources: Array<{ source_id: string; status: string; diff: string | null; [key: string]: unknown }> | null;
  items: Array<{ item_id: string; status: string; revision_state: string; [key: string]: unknown }> | null;
  input_errors: unknown[]; [key: string]: unknown;
};
export type KUBundle = { report: KUReport; handover: string; run: Record<string, unknown> };

export class KUError extends Error {
  constructor(message: string, public status = 0, public code = '', public diagnostic?: unknown) { super(message); this.name = 'KUError'; }
}

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const timestamp = (value: string) => /^[0-9]{1,12}$/.test(value);
const root = '/api/knowledge-update';

function checkSourceId(source: KUSource): void {
  const id = source.source_id;
  let reason = '';
  if (!id || id === '.' || id === '..') reason = '不能为空、点或双点';
  else if (/[/\\:*?"<>|\u0000-\u001f\u007f-\u009f]/.test(id)) reason = '包含路径非法字符或控制字符';
  else if (/[. ]$/.test(id)) reason = '不能以点或空格结尾';
  else if (/^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])$/i.test(id.split('.')[0].replace(/ +$/, ''))) reason = '使用了 Windows 保留名称';
  else if (new TextEncoder().encode(id).length > 255) reason = 'UTF-8 长度超过 255 字节';
  if (reason) throw new KUError(`来源「${source.title}」（ID：${JSON.stringify(id)}）${reason}。`);
}

/** Mirrors Python str.splitlines(), including CRLF and a final line separator. */
export function logicalLines(text: string): string[] {
  if (!text) return [];
  const lines = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\u0085\u2028\u2029]/);
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
}

export function lineCitation(sourceId: string, text: string, first: number, last = first): KUCitation {
  const lines = logicalLines(text);
  const start = Math.min(first, last), end = Math.max(first, last);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end > lines.length) throw new KUError('请选择有效的完整行范围。');
  const selected = lines.slice(start - 1, end);
  // Python splitlines() drops the final separator, so an empty final logical line needs one more.
  const quote = selected.join('\n') + (selected[selected.length - 1] === '' ? '\n' : '');
  return { source_id: sourceId, quote, line_start: start, line_end: end };
}

export function makeKUInput(taskId: string, sources: KUSourceInput[], items: KUItemInput[], mode: KUMode): KUInput {
  if (!taskId.trim()) throw new KUError('请填写任务 ID。');
  if (!sources.length) throw new KUError('请至少选择一个来源。');
  if (!items.length) throw new KUError('请至少填写一个条目。');
  const ids = new Set<string>();
  for (const item of items) {
    if (!item.item_id.trim() || ids.has(item.item_id)) throw new KUError('条目 ID 不能为空或重复。');
    ids.add(item.item_id);
  }
  const files: Record<string, string> = {};
  const paths = new Map<string, string>();
  const declarations = sources.map((row) => {
    const id = row.source.source_id;
    checkSourceId(row.source);
    if (row.old.version === 'none' && row.new.version === 'none') throw new KUError(`${id} 的旧、新版本不能同时为无。`);
    const descriptor = (side: KUSide) => {
      const value = row[side];
      if (value.version === 'none') return null;
      if (!value.version || (value.version !== 'manual' && !timestamp(value.version)) || value.text === null) throw new KUError(`${id} 的${side === 'old' ? '旧' : '新'}版本尚未就绪，请选择版本或手动粘贴文本。`);
      if (value.version === 'manual' && !value.text.trim()) throw new KUError(`${id} 请先粘贴文本；缺失版本请明确选择“无”。`);
      const path = `sources/${id}/${side}.txt`;
      // Only fold ASCII here; Unicode casefold collisions are left to the server.
      const folded = path.replace(/[A-Z]/g, (letter) => letter.toLowerCase());
      const previous = paths.get(folded);
      if (previous !== undefined && previous !== path) throw new KUError(`来源「${row.source.title}」（ID：${id}）生成的路径与 ${previous} 大小写冲突。`);
      paths.set(folded, path);
      files[path] = value.text;
      return { path, version_tag: value.version };
    };
    return { source_id: id, old: descriptor('old'), new: descriptor('new') };
  });
  const task = { schema_version: 'knowledge-update-task/v1', task_id: taskId, sources: declarations,
    items: items.map((item) => ({ item_id: item.item_id, ...(item.question ? { question: item.question } : {}), old_draft: item.old_draft, source_ids: item.source_ids, old_citations: item.old_citations })) };
  const input: KUInput = { task: `${JSON.stringify(task, null, 2)}\n`, files };
  if (mode === 'recheck') input.revisions = `${JSON.stringify({ schema_version: 'knowledge-update-revisions/v1', revisions: items.filter((item) => item.revision.enabled).map((item) => ({ item_id: item.item_id, proposed_draft: item.revision.proposed_draft, citations: item.revision.citations, open_questions: logicalLines(item.revision.open_questions).filter((line) => line.trim()) })) }, null, 2)}\n`;
  return input;
}

function statusMessage(status: number): string {
  switch (status) {
    case 404: return '本站未开启知识更新功能，或所选来源 / 版本不存在。';
    case 503: return '采集服务尚未接入，请手动粘贴文本。';
    case 502: return '采集服务读取失败，请重试；读取失败不表示来源已删除。';
    case 413: return '请求超过 8 MiB，请减少输入文本。';
    case 415: return '服务未接受 JSON 请求格式。';
    case 422: return '输入检查未通过，请查看完整诊断并修正。';
    default: return `知识更新请求失败（HTTP ${status}），输入已保留。`;
  }
}

async function request<T>(path: string, body: unknown, signal: AbortSignal | undefined, read: (response: Response) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) throw new KUError('本次操作已取消。', 0, 'CANCELLED');
  signal?.addEventListener('abort', cancel, { once: true });
  let timedOut = false;
  const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
  try {
    const response = await fetch(`${root}/${path}`, { method: body === undefined ? 'GET' : 'POST', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), signal: controller.signal });
    if (controller.signal.aborted) throw new Error('aborted');
    if (!response.ok) {
      let diagnostic: unknown;
      try { diagnostic = await response.json(); } catch { /* Keep the HTTP status when the response has no JSON. */ }
      const detail = record(diagnostic) && record(diagnostic.detail) ? diagnostic.detail : diagnostic;
      const code = record(detail) && typeof detail.code === 'string' ? detail.code : `HTTP_${response.status}`;
      throw new KUError(statusMessage(response.status), response.status, code, diagnostic);
    }
    const result = await read(response);
    if (controller.signal.aborted) throw new Error('aborted');
    return result;
  } catch (error) {
    if (timedOut) throw new KUError('服务在 30 秒内未返回，已停止等待。输入已保留，可重试。', 0, 'TIMEOUT');
    if (signal?.aborted) throw new KUError('本次操作已取消。', 0, 'CANCELLED');
    if (error instanceof KUError) throw error;
    throw new KUError('无法连接知识更新服务，输入已保留，请检查服务后重试。', 0, 'CONNECTION');
  } finally { window.clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}

async function json(path: string, body?: unknown, signal?: AbortSignal): Promise<unknown> {
  return request(path, body, signal, async (response) => {
    try { return await response.json(); } catch { throw new KUError('服务没有返回有效 JSON。', response.status, 'RESPONSE'); }
  });
}

export async function fetchKUSources(signal?: AbortSignal): Promise<KUSource[]> {
  const data = await json('sources', undefined, signal);
  if (!record(data) || !Array.isArray(data.sources) || !data.sources.every((s) => record(s) && typeof s.source_id === 'string' && typeof s.title === 'string' && typeof s.url === 'string' && typeof s.collector_ready === 'boolean')) throw new KUError('来源清单格式无效。', 0, 'RESPONSE');
  for (const source of data.sources as KUSource[]) checkSourceId(source);
  return data.sources as KUSource[];
}

export async function fetchKUVersions(sourceId: string, signal?: AbortSignal): Promise<KUVersion[]> {
  const data = await json(`sources/${encodeURIComponent(sourceId)}/versions`, undefined, signal);
  if (!record(data) || data.source_id !== sourceId || !Array.isArray(data.versions) || !data.versions.every((v) => record(v) && typeof v.timestamp === 'string' && timestamp(v.timestamp) && typeof v.captured_at === 'string')) throw new KUError('版本列表格式无效。', 0, 'RESPONSE');
  return data.versions as KUVersion[];
}

export async function fetchKUSnapshot(sourceId: string, version: string, signal?: AbortSignal): Promise<KUSnapshot> {
  if (!timestamp(version)) throw new KUError('请选择明确的数值时间戳，不使用 latest。');
  const data = await json(`sources/${encodeURIComponent(sourceId)}/versions/${version}`, undefined, signal);
  if (!record(data) || data.source_id !== sourceId || data.timestamp !== version || typeof data.text !== 'string' || typeof data.captured_at !== 'string' || typeof data.sha256 !== 'string' || typeof data.size !== 'number') throw new KUError('快照格式或版本绑定无效。', 0, 'RESPONSE');
  return data as KUSnapshot;
}

export async function runKU(mode: KUMode, input: KUInput, signal?: AbortSignal): Promise<KUBundle> {
  const data = await json(mode, input, signal);
  if (!record(data) || !record(data.report) || !record(data.run) || typeof data.handover !== 'string' || data.report.mode !== mode || data.report.run_status !== 'COMPLETED' || !record(data.report.summary) || !record(data.report.bindings) || !Array.isArray(data.report.sources) || !Array.isArray(data.report.items)) throw new KUError('服务没有返回完整的本次检查结果。', 0, 'RESPONSE', data);
  return data as KUBundle;
}

export async function exportKU(mode: KUMode, input: KUInput, signal?: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  return request('export', { ...input, mode }, signal, async (response) => {
    if (!response.headers.get('Content-Type')?.startsWith('application/zip')) throw new KUError('服务未返回 ZIP，未发起下载。', 0, 'RESPONSE');
    const filename = response.headers.get('Content-Disposition')?.match(/knowledge-update-(?:scan|recheck)-[a-f0-9]{12}\.zip/)?.[0] ?? `knowledge-update-${mode}.zip`;
    return { blob: await response.blob(), filename };
  });
}

export function saveKUZip(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
