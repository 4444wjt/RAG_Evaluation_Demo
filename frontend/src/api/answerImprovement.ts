import { READ_ONLY, STATIC_SITE } from '../config';
import { sessionHeaders } from './session';

export type LiteralCheck = { code: string; passed: boolean };
export type ImprovementItem = {
  row_index: number; question: unknown; original_answer: unknown; contexts: unknown;
  status: 'verified_candidate' | 'no_candidate' | 'review'; candidate_answer: string | null;
  edits: Array<{ start: number; end: number; before: string; after: string; context_index: number; evidence: string }>;
  verification: { status: 'verified' | 'not_applicable'; checks: LiteralCheck[]; semantic_evaluation: 'NOT_EVALUATED' };
  unresolved_reasons: string[];
};
export type ImprovementRecord = {
  schema_version: 1; id: string; created_at: string; parent_record_id: string; parent_result_sha256: string;
  model_name: string; scope: 'single-quantity-literal-v1';
  original_gate: { passed: boolean; message: string; faith_threshold: number | null; ctx_prec_threshold: number | null };
  bindings: { input_sha256: string; generator_version: string; verifier_version: string; record_sha256: string };
  items: ImprovementItem[];
  counts: { total_rows: number; changed_rows: number; verified_rows: number; no_candidate_rows: number; review_rows: number };
  rescoring: { status: 'not_run'; result: null };
};
export type ImprovementVerification = {
  schema_version: 1; improvement_id: string; status: 'verified' | 'failed'; checks: LiteralCheck[];
  items: Array<{ row_index: number; status: 'verified' | 'failed'; checks: LiteralCheck[] }>;
  semantic_evaluation: 'NOT_EVALUATED'; rescoring: { status: 'not_run'; result: null };
};

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const checks = (value: unknown) => Array.isArray(value) && value.every((check) => object(check) && typeof check.code === 'string' && typeof check.passed === 'boolean');
function isRecord(value: unknown): value is ImprovementRecord {
  if (!object(value) || value.schema_version !== 1 || typeof value.id !== 'string' || typeof value.parent_record_id !== 'string'
    || typeof value.model_name !== 'string' || value.scope !== 'single-quantity-literal-v1' || !object(value.original_gate)
    || typeof value.original_gate.passed !== 'boolean' || typeof value.original_gate.message !== 'string' || !object(value.bindings) || !object(value.counts)
    || !object(value.rescoring) || value.rescoring.status !== 'not_run' || value.rescoring.result !== null || !Array.isArray(value.items)) return false;
  return ['total_rows', 'changed_rows', 'verified_rows', 'no_candidate_rows', 'review_rows'].every((key) => Number.isInteger((value.counts as Record<string, unknown>)[key]) && Number((value.counts as Record<string, unknown>)[key]) >= 0)
    && value.items.every((row) => object(row) && Number.isInteger(row.row_index) && ['verified_candidate', 'no_candidate', 'review'].includes(String(row.status))
      && (row.candidate_answer === null || typeof row.candidate_answer === 'string') && Array.isArray(row.unresolved_reasons) && row.unresolved_reasons.every((reason) => typeof reason === 'string')
      && object(row.verification) && row.verification.semantic_evaluation === 'NOT_EVALUATED' && checks(row.verification.checks)
      && Array.isArray(row.edits) && row.edits.every((edit) => object(edit) && Number.isInteger(edit.start) && Number.isInteger(edit.end)
        && typeof edit.before === 'string' && typeof edit.after === 'string' && Number.isInteger(edit.context_index) && typeof edit.evidence === 'string'));
}
async function request(path: string, init: RequestInit): Promise<Response> {
  if (READ_ONLY || STATIC_SITE) throw new Error('请在本地服务中生成或核验候选。');
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...init, headers: { ...Object.fromEntries(new Headers(init.headers)), ...sessionHeaders() } }); } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new Error('无法连接候选服务，请检查服务后重试。');
  }
  if (!response.ok) {
    let message = `候选操作失败（HTTP ${response.status}）。`;
    try { const body: unknown = await response.json(); if (object(body) && typeof body.detail === 'string') message = body.detail; } catch { /* Preserve HTTP status. */ }
    throw new Error(message);
  }
  return response;
}
export async function createImprovement(parent: string, model: string, signal?: AbortSignal): Promise<ImprovementRecord> {
  const response = await request(`/evaluations/${encodeURIComponent(parent)}/improvements`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_name: model }), signal });
  const value: unknown = await response.json();
  if (!isRecord(value) || value.parent_record_id !== parent || value.model_name !== model) throw new Error('候选记录不完整或与当前评测不匹配。');
  return value;
}
export async function verifyImprovement(id: string, signal?: AbortSignal): Promise<ImprovementVerification> {
  const response = await request(`/improvements/${encodeURIComponent(id)}/verify`, { method: 'POST', signal });
  const value: unknown = await response.json();
  if (!object(value) || value.schema_version !== 1 || value.improvement_id !== id || !['verified', 'failed'].includes(String(value.status))
    || !checks(value.checks) || !Array.isArray(value.items) || value.semantic_evaluation !== 'NOT_EVALUATED'
    || !object(value.rescoring) || value.rescoring.status !== 'not_run') throw new Error('候选重核响应不完整。');
  return value as ImprovementVerification;
}
/** Download the server's complete attachment without reserializing or changing its bytes. */
export async function downloadImprovement(id: string, signal?: AbortSignal): Promise<{ sha256: string; size: number } | null> {
  const response = await request(`/improvements/${encodeURIComponent(id)}`, { signal });
  const blob = await response.blob();
  const bytes = await blob.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  if (signal?.aborted) return null;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = `veriscope-improvement-${id}.json`; link.click(); URL.revokeObjectURL(url);
  return { sha256, size: blob.size };
}
