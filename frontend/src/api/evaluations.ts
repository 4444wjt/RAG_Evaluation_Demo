import { isEvaluationResult } from './client';
import type { EvaluateRequest, EvaluationHistory, EvaluationRecord } from './types';
import { BYOK } from '../config';
import { sessionHeaders } from './session';
import { safeByokMessage } from './byok';

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === 'string');
const nullableString = (value: unknown) => value === null || typeof value === 'string';
const nullableNumber = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value));
const recordId = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
const source = (value: unknown) => value === 'evaluation' || value === 'imported';

function isRequest(value: unknown): value is EvaluateRequest {
  return object(value) && nullableString(value.doc_path) && nullableString(value.testset_path)
    && (value.benchmark === null || value.benchmark === 'truthful_qa')
    && typeof value.benchmark_limit === 'number' && Number.isInteger(value.benchmark_limit) && value.benchmark_limit > 0
    && (value.model_names === null || strings(value.model_names))
    && (value.judge_model_name === undefined || nullableString(value.judge_model_name))
    && nullableNumber(value.faith_threshold) && nullableNumber(value.ctx_prec_threshold) && nullableString(value.version_label);
}

export function isEvaluationRecord(value: unknown): value is EvaluationRecord {
  return object(value) && value.schema_version === 1 && recordId(value.id) && typeof value.created_at === 'string'
    && source(value.source) && (value.request === null || isRequest(value.request)) && isEvaluationResult(value.result)
    && nullableString(value.report_html) && nullableString(value.chart_png_base64) && strings(value.artifact_warnings);
}

async function historyResponse(path: string, options?: RequestInit): Promise<Response> {
  let response: Response;
  try { response = await fetch(`/api/evaluations${path}`, { ...options, headers: { ...Object.fromEntries(new Headers(options?.headers)), ...sessionHeaders() } }); }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new Error(BYOK ? '无法连接历史记录服务，请检查连接后重试。' : '无法连接历史记录服务，请确认本地服务已启动。');
  }
  if (!response.ok) {
    if (BYOK) {
      let detail: unknown;
      try { detail = ((await response.json()) as { detail?: unknown }).detail; } catch { /* fallback */ }
      if (typeof detail === 'string' && detail.length <= 500) throw new Error(safeByokMessage(detail));
    }
    if (response.status === 423) throw new Error('公开演示站未开放历史记录，请在本地服务中查看。');
    if (response.status === 404) throw new Error(path.endsWith('/report') ? '该历史记录未保存 HTML 报告，可导入已有报告预览。' : '历史记录不存在，请刷新列表后重试。');
    if (response.status === 422) throw new Error('历史记录 JSON 格式不符合要求，请选择完整评测结果或导出的记录包。');
    throw new Error(BYOK ? '历史记录读取或保存失败，当前评测结果仍保留，请检查服务。' : '历史记录读取或保存失败，当前评测结果仍保留，请检查本地服务。');
  }
  return response;
}

async function recordResponse(path: string, options?: RequestInit): Promise<EvaluationRecord> {
  const response = await historyResponse(path, options);
  let data: unknown;
  try { data = await response.json(); } catch { throw new Error('历史记录 JSON 无法读取，请检查记录文件。'); }
  if (!isEvaluationRecord(data)) throw new Error('历史记录内容不完整，未替换当前结果。');
  return data;
}

export async function fetchEvaluationHistory(signal?: AbortSignal): Promise<EvaluationHistory> {
  const response = await historyResponse('', { signal });
  let data: unknown;
  try { data = await response.json(); } catch { throw new Error('历史列表响应无法读取，请重试。'); }
  if (!object(data) || !Array.isArray(data.records) || !Number.isInteger(data.unreadable_count) || Number(data.unreadable_count) < 0
    || !data.records.every((row) => object(row) && recordId(row.id) && typeof row.created_at === 'string'
      && source(row.source) && nullableString(row.document_name) && strings(row.models) && typeof row.gate_passed === 'boolean'
      && nullableNumber(row.total_elapsed_seconds) && typeof row.has_report === 'boolean' && strings(row.artifact_warnings))) {
    throw new Error('历史列表内容不完整，请检查服务后重试。');
  }
  return data as unknown as EvaluationHistory;
}

export const fetchEvaluationRecord = (id: string, signal?: AbortSignal) => recordResponse(`/${encodeURIComponent(id)}`, { signal });
export const downloadEvaluationRecord = (id: string, signal?: AbortSignal) => recordResponse(`/${encodeURIComponent(id)}/download`, { signal });
export const importEvaluationRecord = (value: unknown, signal?: AbortSignal) => recordResponse('/import', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value), signal,
});

export async function fetchEvaluationReport(id: string, signal?: AbortSignal): Promise<string> {
  const response = await historyResponse(`/${encodeURIComponent(id)}/report`, { signal });
  const html = await response.text();
  if (!html.trim()) throw new Error('该历史记录的报告内容为空，可导入已有报告预览。');
  return html;
}

export function readRecordFile(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('JSON 文件无法读取。'));
    reader.onerror = () => reject(new Error('JSON 文件无法读取。'));
    reader.readAsText(file);
  });
}

export function downloadRecordJson(record: EvaluationRecord) {
  const url = URL.createObjectURL(new Blob([`${JSON.stringify(record, null, 2)}\n`], { type: 'application/json;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `veriscope-evaluation-${record.id}.json`;
  link.click();
  URL.revokeObjectURL(url);
}
