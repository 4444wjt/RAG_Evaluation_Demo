import type { EvaluateRequest, EvaluationResult } from './types';
import { BYOK } from '../config';
import { sessionHeaders } from './session';
import { byokRequestModels, safeByokMessage } from './byok';

const errorMessages: Record<number, string> = {
  403: '文件路径不在允许范围内，请使用项目中的 demo.txt 或 scenarios/ 相对路径。',
  404: '文件不存在，请确认数据已放入项目允许目录。',
  409: '已有评测正在运行，请等待完成后再手动发起评测。',
  422: '参数不符合 API 要求，请检查模型名称、文件格式及参数范围。',
  423: '公开演示站点未开放在线评测。请查看演示快照，或按仓库 README 在本地运行评测。',
  500: '评测未完成，请检查本地服务日志后再决定是否重新执行。',
};

export class ApiError extends Error {
  constructor(public readonly status: number, detail?: string) {
    super(BYOK ? ({ 403: '本次会话无法访问该资料，请重新上传文件。', 404: '资料或任务不存在，请重新上传或检查历史记录。', 422: '配置未被接受：请检查模型名、API Key、文件和参数；自定义接口须由管理员加入允许列表。', 500: '评测未完成，请检查服务状态后再决定是否重新执行。' } as Record<number, string>)[status] ?? errorMessages[status] ?? '服务未能完成请求，请稍后重试。' : errorMessages[status] ?? '服务未能完成请求，请检查本地服务状态。');
    this.name = 'ApiError';
    if (BYOK && detail) this.message = safeByokMessage(detail);
  }
}

/** BYOK endpoints return fixed, client-safe diagnostic strings, never provider exceptions. */
async function responseError(response: Response): Promise<ApiError> {
  let detail: unknown;
  if (BYOK) {
    try { detail = ((await response.json()) as { detail?: unknown }).detail; } catch { /* generic fallback */ }
  }
  return new ApiError(response.status, typeof detail === 'string' && detail.length <= 500 ? detail : undefined);
}

export class ConnectionError extends Error {
  constructor() {
    super('连接中断，评测结果未知。服务端可能仍在运行；再次执行会发起新的评测，请勿立即重复提交。');
    this.name = 'ConnectionError';
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch('/api/health');
    if (!response.ok) return false;
    const data: unknown = await response.json();
    return typeof data === 'object' && data !== null && 'status' in data && data.status === 'ok';
  } catch {
    return false;
  }
}

/** Validate only the public envelope, without changing rows or calculating metrics. */
export function isEvaluationResult(value: unknown): value is EvaluationResult {
  if (typeof value !== 'object' || value === null) return false;
  const result = value as Record<string, unknown>;
  const isRow = (row: unknown) => typeof row === 'object' && row !== null && !Array.isArray(row);
  const isOptionalThreshold = (threshold: unknown) => threshold === undefined || threshold === null
    || (typeof threshold === 'number' && Number.isFinite(threshold));
  return Array.isArray(result.summary) && result.summary.every(isRow)
    && Array.isArray(result.summary_df) && result.summary_df.every(isRow)
    && typeof result.gate_passed === 'boolean' && typeof result.gate_message === 'string'
    && (result.chart_path === null || typeof result.chart_path === 'string')
    && (result.html_path === null || typeof result.html_path === 'string')
    && isRow(result.model_dfs) && Object.values(result.model_dfs as Record<string, unknown>).every((rows) => Array.isArray(rows) && rows.every(isRow))
    && (result.total_elapsed_seconds === null || (typeof result.total_elapsed_seconds === 'number' && Number.isFinite(result.total_elapsed_seconds)))
    && isOptionalThreshold(result.faith_threshold) && isOptionalThreshold(result.ctx_prec_threshold);
}

export async function evaluate(request: EvaluateRequest, onProgress?: (message: string) => void): Promise<EvaluationResult> {
  // Credentials are added only to the transient POST, never to the retained request/config.
  const body = BYOK ? { ...request, byok_models: byokRequestModels(request.model_names ?? [], request.judge_model_name) } : request;
  let response: Response;
  try {
    response = await fetch('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ConnectionError();
  }
  if (!response.ok) throw await responseError(response);
  let data: unknown;
  try { data = await response.json(); } catch { throw new ConnectionError(); }
  if (BYOK && response.status === 202) {
    if (typeof data !== 'object' || data === null || !('job_id' in data) || typeof data.job_id !== 'string' || !/^[a-f0-9]{32}$/.test(data.job_id)) throw new ConnectionError();
    const id = data.job_id;
    onProgress?.('评测任务已接收，正在等待服务处理…');
    while (true) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      let jobResponse: Response;
      try { jobResponse = await fetch(`/api/jobs/${encodeURIComponent(id)}`, { headers: sessionHeaders() }); } catch { throw new ConnectionError(); }
      if (!jobResponse.ok) throw await responseError(jobResponse);
      let job: unknown;
      try { job = await jobResponse.json(); } catch { throw new ConnectionError(); }
      if (typeof job !== 'object' || job === null || !('status' in job)) throw new ConnectionError();
      const progress = job as Record<string, unknown>;
      if (typeof progress.message === 'string') onProgress?.(safeByokMessage(progress.message));
      if (progress.status === 'completed') { data = progress.result; break; }
      if (progress.status === 'failed') throw new Error(typeof progress.error === 'string' && progress.error.length <= 500 ? safeByokMessage(progress.error) : '评测未完成，请检查模型凭据、额度、资料和接口配置后手动重试。');
      if (progress.status !== 'running') throw new ConnectionError();
    }
  }
  if (!isEvaluationResult(data)) throw new Error('服务返回的结果结构不完整，请检查服务日志。');
  return data;
}
