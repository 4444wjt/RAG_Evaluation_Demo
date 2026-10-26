import { READ_ONLY, STATIC_SITE } from '../config';

/** Mirrors backend/api/parent_context.py: every file, and all four together, stay within 8 MiB of raw bytes. */
export const PARENT_CONTEXT_MAX_BYTES = 8 * 1024 * 1024;
/** The default limit in backend/core/parent_context.py. Over it the whole package stays in review; nothing is truncated. */
export const PARENT_CONTEXT_LIMIT_BYTES = 24000;
export const PUBLIC_DEMO_MESSAGE = '公开演示不接收文件，请在本地运行后导入。';

export const PARENT_CONTEXT_FILE_KEYS = ['source', 'segments', 'retrieval', 'binding'] as const;
export type ParentContextFileKey = typeof PARENT_CONTEXT_FILE_KEYS[number];
export type ParentContextFiles = Record<ParentContextFileKey, Uint8Array<ArrayBuffer>>;
export type ParentContextStatus = 'PREPARED_FOR_REVIEW' | 'REVIEW';

const FILE_LABELS: Record<ParentContextFileKey, string> = {
  source: '原文 source.txt',
  segments: '段快照 segments.json',
  retrieval: '检索命中 retrieval.json',
  binding: '绑定声明 binding.json',
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}

/** Checked before any request: a missing, empty or oversized file never reaches the API. */
export function parentContextFilesProblem(sizes: Partial<Record<ParentContextFileKey, number>>): string | null {
  const missing = PARENT_CONTEXT_FILE_KEYS.filter((key) => sizes[key] === undefined);
  if (missing.length) return `还需导入：${missing.map((key) => FILE_LABELS[key]).join('、')}。`;
  for (const key of PARENT_CONTEXT_FILE_KEYS) {
    if (!sizes[key]) return `${FILE_LABELS[key]} 为空，请重新选择。`;
    if (sizes[key]! > PARENT_CONTEXT_MAX_BYTES) return `${FILE_LABELS[key]} 超过 8 MiB，请减小后重新导入。`;
  }
  const total = PARENT_CONTEXT_FILE_KEYS.reduce((sum, key) => sum + sizes[key]!, 0);
  if (total > PARENT_CONTEXT_MAX_BYTES) return `四份文件合计 ${formatBytes(total)}，超过 8 MiB 上限，请减小后重新导入。`;
  return null;
}

/** Raw bytes, never File.text(): a BOM, CRLF line endings and the hashes bound to them must reach the API unchanged. */
export function readFileBytes(file: File): Promise<Uint8Array<ArrayBuffer>> {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer().then((buffer) => new Uint8Array(buffer));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => reader.result === null || typeof reader.result === 'string' ? reject(new Error('文件内容无法读取')) : resolve(new Uint8Array(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('文件读取失败'));
    reader.onabort = () => reject(new Error('文件读取已取消'));
    reader.readAsArrayBuffer(file);
  });
}

function toBase64(bytes: Uint8Array<ArrayBuffer>): string {
  let binary = '';
  for (let start = 0; start < bytes.length; start += 0x8000) binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(binary);
}

export interface PreparedLine {
  position: number;
  segmentId: string;
  text: string;
  hit: boolean;
  metadata: boolean;
}

export interface ParentContextResult {
  status: ParentContextStatus;
  diagnostics: string[];
  limitations: string[];
  inputsSha256: Record<ParentContextFileKey, string>;
  /** Present only for PREPARED_FOR_REVIEW; one line per expanded segment, aligned with its source position. */
  context: { sha256: string; bytes: number; text: string; lines: PreparedLine[] } | null;
  facts: { retrieved: number; expanded: number; sourceSegments: number; documentId?: string; datasetId?: string; messageId?: string; conversationId?: string } | null;
  normalization: { bomIgnored: boolean; crlfMapped: boolean } | null;
  /** 'matched': the browser recomputed every input hash and the context hash; 'unavailable': no Web Crypto here. */
  hashCheck: 'matched' | 'unavailable';
  /** The report exactly as returned, for report.json. */
  report: Record<string, unknown>;
}

export class ParentContextRequestError extends Error {
  readonly code: string;
  constructor(message: string, code: string) {
    super(message);
    this.name = 'ParentContextRequestError';
    this.code = code;
  }
}

const MALFORMED = '本地服务返回的结果不完整或不符合约定，未采用。请确认后端为最新版本后重试。';
const FAILURES: Record<number, string> = {
  404: '当前后端未提供父条资料准备，请启动最新版本的本地服务。',
  413: '四份文件合计不能超过 8 MiB。',
  415: '请以四份原始文件提交父条资料准备请求。',
  422: '文件请求不符合格式，请核对四份文件、文件编码和大小。',
  423: PUBLIC_DEMO_MESSAGE,
};

async function failureMessage(response: Response): Promise<string> {
  let detail: unknown;
  try {
    const body: unknown = await response.json();
    detail = isRecord(body) ? body.detail : undefined;
  } catch { detail = undefined; }
  // The backend sends fixed, client-safe Chinese messages; anything else falls back to the status.
  if (typeof detail === 'string' && detail.trim() && detail.length <= 300) return detail.trim();
  return FAILURES[response.status] ?? (response.status >= 500 ? '本地服务处理失败，请查看后端日志后重试。' : '父条资料准备未完成，请检查本地后端后重试。');
}

const HEX64 = /^[a-f0-9]{64}$/;
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const isCount = (value: unknown, min = 0): value is number => Number.isSafeInteger(value) && (value as number) >= min;
const isText = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const isIdList = (value: unknown): value is string[] => Array.isArray(value) && value.length > 0 && value.every(isText);
const optionalText = (value: unknown) => isText(value) ? value : undefined;

/** HTTP 200 is not success by itself: the body must match ParentContextResponseV1 and its own invariants. */
function readResponse(value: unknown): Omit<ParentContextResult, 'hashCheck'> | null {
  if (!isRecord(value) || Object.keys(value).length !== 3 || value.schema !== 'ParentContextResponseV1' || !('context_text' in value)) return null;
  const report = value.report;
  const text = value.context_text;
  if (!isRecord(report) || report.schema !== 'ParentContextPreparationV1') return null;
  // Fixed boundaries: never a verified provider request, index state, completeness proof, human label or gate result.
  if (report.provider_wire_verified !== false || report.parent_completeness_verified !== false || report.human_labels !== null || report.gate_result !== null
    || ('online_index_verified' in report && report.online_index_verified !== false)) return null;
  const status = report.status;
  if (status !== 'PREPARED_FOR_REVIEW' && status !== 'REVIEW') return null;
  const diagnostics = report.diagnostics;
  if (!Array.isArray(diagnostics) || !diagnostics.every((item) => isRecord(item) && isText(item.code))) return null;
  const limitations = report.limitations;
  if (!Array.isArray(limitations) || !limitations.every(isText)) return null;
  const hashes = report.inputs_sha256;
  if (!isRecord(hashes) || Object.keys(hashes).length !== PARENT_CONTEXT_FILE_KEYS.length
    || !PARENT_CONTEXT_FILE_KEYS.every((key) => typeof hashes[key] === 'string' && HEX64.test(hashes[key]))) return null;
  const normalization = isRecord(report.source_normalization)
    ? { bomIgnored: report.source_normalization.utf8_bom_removed_for_mapping === true, crlfMapped: report.source_normalization.crlf_to_lf_for_mapping === true }
    : null;
  const base = {
    // An object literal widens the narrowed status back to string.
    status: status as ParentContextStatus,
    diagnostics: diagnostics.map((item) => (item as { code: string }).code),
    limitations: limitations as string[],
    inputsSha256: Object.fromEntries(PARENT_CONTEXT_FILE_KEYS.map((key) => [key, hashes[key] as string])) as Record<ParentContextFileKey, string>,
    normalization,
    report,
  };
  // REVIEW keeps its diagnostics and never carries partial material.
  if (status === 'REVIEW') return report.context === null && text === null && diagnostics.length > 0 ? { ...base, context: null, facts: null } : null;

  const context = report.context;
  if (!isRecord(context) || context.file !== 'context.txt' || typeof context.sha256 !== 'string' || !HEX64.test(context.sha256)
    || !isCount(context.utf8_bytes) || context.utf8_bytes > PARENT_CONTEXT_LIMIT_BYTES || typeof text !== 'string' || diagnostics.length > 0) return null;
  if (new TextEncoder().encode(text).length !== context.utf8_bytes) return null;
  const retrieved = report.retrieved_segment_ids;
  const expanded = report.expanded_segment_ids;
  const positions = report.expanded_source_positions;
  const total = report.source_segment_count;
  const metadata = report.metadata_positions ?? [];
  if (!isIdList(retrieved) || !isIdList(expanded) || !isCount(total, 1) || !Array.isArray(positions) || positions.length !== expanded.length
    || !positions.every((position) => isCount(position, 1) && position <= total) || !Array.isArray(metadata) || !metadata.every((position) => isCount(position, 1))) return null;
  const expandedIds = new Set(expanded);
  if (!retrieved.every((id) => expandedIds.has(id))) return null;
  // Each segment is one source line and the material joins them with LF, so lines and positions must line up.
  const lines = text.split('\n');
  if (lines.length !== expanded.length) return null;
  const hits = new Set(retrieved);
  const metadataPositions = new Set(metadata as number[]);
  return {
    ...base,
    context: {
      sha256: context.sha256,
      bytes: context.utf8_bytes,
      text,
      lines: lines.map((line, index) => ({ position: positions[index] as number, segmentId: expanded[index], text: line, hit: hits.has(expanded[index]), metadata: metadataPositions.has(positions[index] as number) })),
    },
    facts: {
      // A segment returned twice is still one hit; expanded counts the lines actually written to context.txt.
      retrieved: hits.size, expanded: expanded.length, sourceSegments: total,
      documentId: optionalText(report.document_id), datasetId: optionalText(report.dataset_id),
      messageId: optionalText(report.message_id), conversationId: optionalText(report.conversation_id),
    },
  };
}

async function sha256Hex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** A result must belong to the four files on screen; without Web Crypto the page says so instead of claiming a match. */
async function compareHashes(files: ParentContextFiles, result: Omit<ParentContextResult, 'hashCheck'>): Promise<'matched' | 'mismatch' | 'unavailable'> {
  if (!globalThis.crypto?.subtle) return 'unavailable';
  let digests: string[];
  try {
    digests = await Promise.all([
      ...PARENT_CONTEXT_FILE_KEYS.map((key) => sha256Hex(files[key])),
      ...(result.context ? [sha256Hex(new TextEncoder().encode(result.context.text))] : []),
    ]);
  } catch { return 'unavailable'; }
  const inputs = PARENT_CONTEXT_FILE_KEYS.every((key, index) => digests[index] === result.inputsSha256[key]);
  return inputs && (!result.context || digests[PARENT_CONTEXT_FILE_KEYS.length] === result.context.sha256) ? 'matched' : 'mismatch';
}

export async function prepareParentContext(files: ParentContextFiles, signal?: AbortSignal): Promise<ParentContextResult> {
  if (READ_ONLY || STATIC_SITE) throw new ParentContextRequestError(PUBLIC_DEMO_MESSAGE, 'READ_ONLY');
  const problem = parentContextFilesProblem(Object.fromEntries(PARENT_CONTEXT_FILE_KEYS.map((key) => [key, files[key]?.length])));
  if (problem) throw new ParentContextRequestError(problem, 'FILES');
  const body = JSON.stringify({
    schema: 'ParentContextRequestV1',
    files: Object.fromEntries(PARENT_CONTEXT_FILE_KEYS.map((key) => [key, toBase64(files[key])])),
    max_context_bytes: PARENT_CONTEXT_LIMIT_BYTES,
  });
  let response: Response;
  try {
    response = await fetch('/api/contexts/parent/prepare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ParentContextRequestError('无法连接本地服务，请确认后端已启动后重试。', 'NETWORK');
  }
  if (!response.ok) throw new ParentContextRequestError(await failureMessage(response), `HTTP ${response.status}`);
  let value: unknown;
  try { value = await response.json(); } catch (error) {
    if (signal?.aborted) throw error;
    throw new ParentContextRequestError(MALFORMED, 'MALFORMED_RESPONSE');
  }
  const result = readResponse(value);
  if (!result) throw new ParentContextRequestError(MALFORMED, 'MALFORMED_RESPONSE');
  const hashCheck = await compareHashes(files, result);
  if (hashCheck === 'mismatch') throw new ParentContextRequestError('返回结果与当前四份文件或资料内容的哈希不一致，未采用。请重新准备。', 'HASH_MISMATCH');
  return { ...result, hashCheck };
}

/** Problem and recovery for every code backend/core/parent_context.py can return; the code itself stays in the details. */
const DIAGNOSTICS: Record<string, [problem: string, fix: string]> = {
  INVALID_CONTEXT_LIMIT: ['资料字节上限无效。', '刷新页面后重试；仍出现时请确认前端与后端版本一致。'],
  INPUT_TOO_LARGE: ['有文件超过 8 MiB。', '减小文件后重新导入。'],
  CONTEXT_LIMIT_EXCEEDED: ['补全后的父条资料超过 24000 UTF-8 字节，整包待复核，没有截断。', '减少命中段，或拆成多次检索分别准备。'],
  DUPLICATE_JSON_FIELD: ['JSON 文件含重复字段。', '删除重复字段后重新导入。'],
  NON_FINITE_JSON: ['JSON 文件含 NaN 或 Infinity。', '改为有限数值后重新导入。'],
  INVALID_JSON: ['JSON 文件无法解析。', '确认文件为 UTF-8 编码的标准 JSON 后重新导入。'],
  OBJECT_REQUIRED: ['JSON 文件顶层不是对象。', '确认导入的是完整导出文件，而不是其中一段。'],
  BINDING_FIELDS_UNSUPPORTED: ['绑定声明的字段与 ParentContextBindingV1 不一致。', '用 scripts/parent_context.py bind 重新生成绑定声明。'],
  BINDING_VERSION_UNSUPPORTED: ['绑定声明不是 ParentContextBindingV1。', '用 scripts/parent_context.py bind 重新生成绑定声明。'],
  SOURCE_FORMAT_UNSUPPORTED: ['绑定声明的原文格式不是 blank-line-articles-v1。', '按“完整条之间空一行”整理原文后重新绑定。'],
  STRUCTURE_DECLARATION_REQUIRED: ['绑定声明缺少“完整父条边界”的操作者声明。', '核对原文后用 --assert-complete 重新绑定。'],
  INVALID_BINDING_HASH: ['绑定声明中的哈希格式无效。', '用 scripts/parent_context.py bind 重新生成绑定声明。'],
  SOURCE_HASH_MISMATCH: ['原文与绑定声明登记的哈希不一致，文件可能在登记后改动过。', '换回登记时的原文；确有更新时先核对，再重新绑定。'],
  SEGMENTS_HASH_MISMATCH: ['段快照与绑定声明登记的哈希不一致。', '换回登记时的段快照；确有更新时先核对，再重新绑定。'],
  INVALID_DOCUMENT_IDENTITY: ['绑定声明中的文档或数据集标识无效。', '填写真实的 document_id 与 dataset_id 后重新绑定。'],
  INVALID_METADATA_DECLARATION: ['绑定声明中的元数据行位置无效。', '用不重复的正整数位置重新绑定。'],
  SOURCE_NOT_UTF8: ['原文不是 UTF-8 编码。', '转为 UTF-8 后重新绑定。'],
  SOURCE_NEWLINE_UNSUPPORTED: ['原文含单独的回车符（CR）。', '统一为 LF 或 CRLF 换行后重新绑定。'],
  SOURCE_CONTROL_CHARACTER: ['原文含空字符（U+0000）。', '删除空字符后重新绑定。'],
  INCOMPLETE_SEGMENT_EXPORT: ['段快照不是完整的一页导出：需要 page 与 total_pages 均为 1，total 等于段数。', '重新导出全部段。'],
  INVALID_SEGMENTS: ['段快照没有段列表，或超过 10000 段。', '检查 data 字段后重新导出。'],
  INVALID_SEGMENT: ['段快照中有段不是对象。', '重新导出段快照。'],
  INVALID_SEGMENT_ID_OR_POSITION: ['有段缺少有效 id，或 position 不是正整数。', '重新导出段快照。'],
  SEGMENT_DOCUMENT_MISMATCH: ['有段的 document_id 与绑定声明不一致。', '确认段快照和绑定声明属于同一份文档。'],
  INVALID_SEGMENT_CONTENT: ['有段内容为空。', '重新导出段快照。'],
  SEGMENT_NOT_ACTIVE: ['有段未启用，或状态不是 completed。', '启用并完成索引后重新导出。'],
  QA_OR_SUMMARY_UNSUPPORTED: ['段或命中带有问答、摘要内容，暂不支持。', '使用普通分段模式重新导出。'],
  SOURCE_SEGMENTS_OR_BLOCKS_MISMATCH: ['原文的非空行与段快照不能逐行对应：位置不连续、文字不同或分块不符。', '逐行核对原文与段内容；原文行首尾不能有空白。'],
  UNCLASSIFIED_SOURCE_LINES: ['声明的元数据行与原文中的独立标题或来源段不一致。', '修正 --metadata-positions 后重新绑定。'],
  MISSING_OR_DUPLICATE_PARENT_HEADER: ['原文缺少“第X条”条首，或条号重复。', '检查原文条首格式后重新绑定。'],
  RETRIEVAL_FORMAT_UNSUPPORTED: ['检索命中不是 CitationResourcesV1 格式。', '按接入说明重新导出检索命中。'],
  SINGLE_RECORD_REQUIRED: ['检索命中需要恰好一条记录。', '每次只导入一次检索的结果。'],
  RETRIEVAL_IDENTITY_MISSING: ['检索记录缺少 message_id 或 conversation_id。', '导出带真实标识的检索记录。'],
  EMPTY_OR_TOO_MANY_RESOURCES: ['命中资源为空，或超过 20 个。', '检查 resources 字段。'],
  INVALID_RESOURCE: ['有命中资源缺少有效的 segment_id。', '重新导出检索命中。'],
  RESOURCE_DOCUMENT_OR_DATASET_MISMATCH: ['命中资源的文档或数据集与绑定声明不一致。', '确认命中来自绑定的同一份文档。'],
  UNKNOWN_SEGMENT_ID: ['有命中段不在段快照中。', '使用与这次检索同一时期导出的段快照。'],
  RESOURCE_TEXT_MISMATCH: ['命中段文字与段快照不一致，索引可能已更新。', '重新导出段快照和检索命中。'],
  INPUT_IO_FAILURE: ['文件读取失败。', '检查文件后重试。'],
};

export function diagnosticMessage(code: string): { problem: string; fix: string } {
  const [problem, fix] = DIAGNOSTICS[code] ?? ['未识别的诊断码。', '下载报告查看详情，核对四份文件后重新准备。'];
  return { problem, fix };
}

export type ParentContextDecision = 'usable_for_downstream' | 'needs_further_check';
export interface ParentContextReview {
  identity: string;
  decision: ParentContextDecision | '';
  note: string;
  checked: boolean;
}
export const EMPTY_REVIEW: ParentContextReview = { identity: '', decision: '', note: '', checked: false };

/** Only PREPARED_FOR_REVIEW may be marked usable, and only after the checklist; "still needs checking" always needs a reason. */
export function reviewProblem(status: ParentContextStatus, review: ParentContextReview): string | null {
  if (!review.identity.trim()) return '请填写复核人姓名或标识。';
  if (!review.decision) return '请选择复核意见。';
  if (review.decision === 'usable_for_downstream') {
    if (status !== 'PREPARED_FOR_REVIEW') return '待复核结果只能选择“仍需核查”。';
    if (!review.checked) return '请勾选“已核对来源、父条范围和预览”。';
  } else if (!review.note.trim()) return '选择“仍需核查”时，请写明原因。';
  return null;
}

/** The identity is self-reported: the record never claims an independent human check, a gate result or human labels. */
export function buildReviewRecord(result: ParentContextResult, review: ParentContextReview, timestamp: string) {
  const problem = reviewProblem(result.status, review);
  if (problem) throw new Error(problem);
  return {
    schema: 'ParentContextReviewV1',
    timestamp,
    result_status: result.status,
    inputs_sha256: { ...result.inputsSha256 },
    context_sha256: result.context?.sha256 ?? null,
    decision: review.decision as ParentContextDecision,
    note: review.note.trim(),
    reviewer: { identity: review.identity.trim(), independentHumanVerified: false },
    sources_scope_preview_checked: review.checked,
    diagnostic_codes: [...result.diagnostics],
    model_calls: 0,
    gate_result: null,
    human_labels: null,
  };
}
