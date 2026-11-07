import { BYOK, READ_ONLY, STATIC_SITE } from '../config';

export type RecognitionKind = 'ocr' | 'asr';
export const RECOGNITION_MESSAGES = {
  ok: '识别完成，已生成评测文字。',
  plugin_missing: '本机未配置可用的识别插件，请先安装并登记。',
  bad_input: '文件无法识别，请检查内容；音频须为 16kHz 单声道 WAV 或 FLAC。',
  timeout: '识别超时，插件已停止。请检查文件后手动重试。',
  plugin_error: '识别插件运行失败，请检查本地服务日志。',
  bad_output: '识别输出格式不符合约定，结果未采用。',
  binding_mismatch: '识别引擎或模型与本机登记不一致，结果未采用。',
  input_mismatch: '识别结果与上传原件不一致，结果未采用。',
  empty_text: '识别完成，但没有识别出文字。请更换清晰的原件。',
} as const;
export type RecognitionStatus = keyof typeof RECOGNITION_MESSAGES;

interface RecognitionBase { status: RecognitionStatus; kind: RecognitionKind }
export type RecognitionOutcome = (RecognitionBase & { status: Exclude<RecognitionStatus, 'ok'> })
  | (RecognitionBase & { status: 'ok'; text_path: string; recognition_path: string });
export type Point = [number, number];
export interface SourceRef {
  page?: number;
  line_id?: string;
  box?: Point[];
  segment_id?: string;
  start_ms?: number;
  end_ms?: number;
  conf: number | null;
}
export interface RecognitionSpan extends SourceRef {
  start: number;
  end: number;
  char_conf: (number | null)[] | null;
}
interface FileBinding { relative_path: string; sha256: string }
export interface RecognitionSidecar {
  schema: 'veriscope.recognition-sidecar.v1';
  source: FileBinding;
  text: FileBinding;
  spans: RecognitionSpan[];
  elapsed_ms: number;
  recognition: {
    kind: RecognitionKind;
    input: { width: number | null; height: number | null; duration_ms: number | null };
    engine: { name: string; version: string; backend: string };
    warnings: string[];
  };
}
export interface RecognizedDocument { sidecar: RecognitionSidecar; text: string }
export type LocationStatus = 'located' | 'ambiguous' | 'not_found' | 'empty_quote' | 'invalid_quote';
export interface SourceMatch { start: number; end: number; refs: SourceRef[]; min_conf: number | null; conf_missing: boolean }
export interface LocatedStatement {
  text?: unknown;
  verdict?: unknown;
  evidence?: unknown;
  source_locations: { status: LocationStatus; matches: SourceMatch[] };
}
export interface SourceLocations {
  schema: 'veriscope.source-locations.v1';
  parent_record_id: string;
  recognition_path: string;
  source: FileBinding;
  text: FileBinding;
  models: Record<string, { row_index: number; trace_status: 'available' | 'missing' | 'invalid'; statements: LocatedStatement[] | null }[]>;
}

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const integer = (value: unknown): value is number => finite(value) && Number.isInteger(value) && value >= 0;
const confidence = (value: unknown) => value === null || (finite(value) && value >= 0 && value <= 1);
const sha = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const upload = (path: unknown, suffix: string) => typeof path === 'string' ? new RegExp(`^work/uploads/([a-f0-9]{32})\\.(${suffix})$`).exec(path) : null;
const sidecarId = (path: unknown) => upload(path, 'recognition\\.json')?.[1];
const malformed = () => new Error('识别或定位结果不完整，尚未采用；请检查本地服务。');

function available() {
  if (READ_ONLY || STATIC_SITE) throw new Error('公开演示不提供本机识别，请在本地运行。');
  if (BYOK) throw new Error('访客模式不提供本机识别，请上传已提取的文字资料。');
}

async function request(path: string, options: RequestInit): Promise<Response> {
  available();
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...options, cache: 'no-store' }); }
  catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error('无法连接本机识别服务，请检查服务后手动重试。');
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      403: '当前模式或资料权限不允许本机识别。',
      404: '原件、识别结果或已保存定位不存在，请检查本地材料。',
      409: '记录或识别材料已变化，旧定位不能继续使用。请保留材料并重新建立定位。',
      422: '材料格式、定位结构或评测文档绑定不符合要求，结果未采用。',
      423: '公开演示不提供本机识别，请在本地运行。',
    };
    throw new Error(messages[response.status] ?? '本机识别服务未能完成请求，请检查服务后手动重试。');
  }
  return response;
}

async function json(path: string, options: RequestInit): Promise<unknown> {
  const response = await request(path, options);
  try { return await response.json(); } catch { throw malformed(); }
}

function validRef(value: unknown): value is SourceRef {
  if (!object(value) || !confidence(value.conf)) return false;
  if (typeof value.line_id === 'string' && integer(value.page) && value.page > 0) {
    return Array.isArray(value.box) && value.box.length === 4
      && value.box.every((point) => Array.isArray(point) && point.length === 2 && point.every(finite));
  }
  return typeof value.segment_id === 'string' && integer(value.start_ms) && integer(value.end_ms) && value.end_ms >= value.start_ms;
}

export async function recognizeInput(kind: RecognitionKind, relativePath: string, signal?: AbortSignal): Promise<RecognitionOutcome> {
  available();
  const id = upload(relativePath, kind === 'ocr' ? 'png|jpg|jpeg' : 'wav|flac')?.[1];
  if (!id) throw new Error('请选择通过导入入口上传的图片或音频。');
  const result = await json('/recognitions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, relative_path: relativePath }), signal });
  if (!object(result) || result.kind !== kind || typeof result.status !== 'string' || !Object.hasOwn(RECOGNITION_MESSAGES, result.status)) throw malformed();
  if (result.status === 'ok' && (result.text_path !== `work/uploads/${id}.txt` || result.recognition_path !== `work/uploads/${id}.recognition.json`)) throw malformed();
  return result as unknown as RecognitionOutcome;
}

export async function fetchRecognition(relativePath: string, signal?: AbortSignal): Promise<RecognizedDocument> {
  available();
  const id = sidecarId(relativePath);
  if (!id) throw malformed();
  const result = await json(`/recognitions/sidecar?${new URLSearchParams({ relative_path: relativePath })}`, { signal });
  if (!object(result) || typeof result.text !== 'string' || !object(result.sidecar)) throw malformed();
  const sidecar = result.sidecar;
  if (sidecar.schema !== 'veriscope.recognition-sidecar.v1' || !object(sidecar.source) || !object(sidecar.text)
    || sidecar.text.relative_path !== `work/uploads/${id}.txt` || !sha(sidecar.text.sha256) || !sha(sidecar.source.sha256)
    || !finite(sidecar.elapsed_ms) || sidecar.elapsed_ms < 0 || !object(sidecar.recognition)) throw malformed();
  const recognition = sidecar.recognition;
  if ((recognition.kind !== 'ocr' && recognition.kind !== 'asr') || !object(recognition.input)
    || upload(sidecar.source.relative_path, recognition.kind === 'ocr' ? 'png|jpg|jpeg' : 'wav|flac')?.[1] !== id
    || !object(recognition.engine) || !['name', 'version', 'backend'].every((key) => typeof recognition.engine === 'object' && recognition.engine !== null && typeof (recognition.engine as Record<string, unknown>)[key] === 'string')
    || !Array.isArray(recognition.warnings) || !recognition.warnings.every((value) => typeof value === 'string')) throw malformed();
  const input = recognition.input;
  if (recognition.kind === 'ocr'
    ? !(integer(input.width) && input.width > 0 && integer(input.height) && input.height > 0)
    : !(integer(input.duration_ms) && input.duration_ms > 0)) throw malformed();
  const length = Array.from(result.text).length;
  if (!Array.isArray(sidecar.spans) || !sidecar.spans.every((span) => object(span) && validRef(span)
    && integer(span.start) && integer(span.end) && span.end >= span.start && span.end <= length
    && (span.char_conf === null || (Array.isArray(span.char_conf) && span.char_conf.every(confidence))))) throw malformed();
  return result as unknown as RecognizedDocument;
}

export async function fetchRecognitionMedia(relativePath: string, expectedSha: string, signal?: AbortSignal): Promise<Blob> {
  available();
  if (!upload(relativePath, 'png|jpg|jpeg|wav|flac') || !sha(expectedSha)) throw malformed();
  const response = await request(`/inputs/media?${new URLSearchParams({ relative_path: relativePath })}`, { signal });
  if (response.headers.get('X-Content-SHA256') !== expectedSha) throw new Error('原件字节与识别材料不一致，原件回看未采用。');
  return response.blob();
}

function locations(value: unknown, recordId: string, recognitionPath: string): SourceLocations {
  const id = sidecarId(recognitionPath);
  if (!object(value) || value.schema !== 'veriscope.source-locations.v1' || value.parent_record_id !== recordId
    || value.recognition_path !== recognitionPath || !object(value.source) || !object(value.text)
    || !sha(value.source.sha256) || !sha(value.text.sha256) || value.text.relative_path !== `work/uploads/${id}.txt`
    || upload(value.source.relative_path, 'png|jpg|jpeg|wav|flac')?.[1] !== id || !object(value.models)) throw malformed();
  for (const rows of Object.values(value.models)) {
    if (!Array.isArray(rows)) throw malformed();
    for (const row of rows) {
      if (!object(row) || !integer(row.row_index) || !['available', 'missing', 'invalid'].includes(String(row.trace_status))) throw malformed();
      if (row.trace_status !== 'available') { if (row.statements !== null) throw malformed(); continue; }
      if (!Array.isArray(row.statements)) throw malformed();
      for (const statement of row.statements) {
        if (!object(statement) || !object(statement.source_locations)) throw malformed();
        const attached = statement.source_locations;
        if (!['located', 'ambiguous', 'not_found', 'empty_quote', 'invalid_quote'].includes(String(attached.status))
          || !Array.isArray(attached.matches) || !attached.matches.every((match) => object(match) && integer(match.start)
            && integer(match.end) && match.end >= match.start && confidence(match.min_conf) && typeof match.conf_missing === 'boolean'
            && Array.isArray(match.refs) && match.refs.every(validRef))) throw malformed();
      }
    }
  }
  return value as unknown as SourceLocations;
}

export async function fetchSourceLocations(recordId: string, recognitionPath: string, rebuild: boolean, signal?: AbortSignal): Promise<SourceLocations> {
  available();
  if (!/^[a-f0-9]{32}$/.test(recordId) || !sidecarId(recognitionPath)) throw malformed();
  const result = await json(`/evaluations/${recordId}/source-locations`, rebuild
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ recognition_path: recognitionPath }), signal }
    : { signal });
  return locations(result, recordId, recognitionPath);
}
