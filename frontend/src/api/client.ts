import type { EvaluateRequest, EvaluationResult } from './types';

const errorMessages: Record<number, string> = {
  403: '文件路径不在允许范围内，请使用项目中的 demo.txt 或 scenarios/ 相对路径。',
  404: '文件不存在，请确认数据已放入项目允许目录。',
  409: '已有评测正在运行，请等待完成后再手动发起评测。',
  422: '参数不符合 API 要求，请检查模型名称、文件格式及参数范围。',
  423: '公开演示站点未开放在线评测。请查看演示快照，或按仓库 README 在本地运行评测。',
  500: '评测未完成，请检查本地服务日志后再决定是否重新执行。',
};

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super(errorMessages[status] ?? '服务未能完成请求，请检查本地服务状态。');
    this.name = 'ApiError';
  }
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
  return Array.isArray(result.summary) && result.summary.every(isRow)
    && Array.isArray(result.summary_df) && result.summary_df.every(isRow)
    && typeof result.gate_passed === 'boolean' && typeof result.gate_message === 'string'
    && (result.chart_path === null || typeof result.chart_path === 'string')
    && (result.html_path === null || typeof result.html_path === 'string')
    && isRow(result.model_dfs) && Object.values(result.model_dfs as Record<string, unknown>).every((rows) => Array.isArray(rows) && rows.every(isRow))
    && (result.total_elapsed_seconds === null || (typeof result.total_elapsed_seconds === 'number' && Number.isFinite(result.total_elapsed_seconds)));
}

export async function evaluate(request: EvaluateRequest): Promise<EvaluationResult> {
  let response: Response;
  try {
    response = await fetch('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  } catch {
    throw new ConnectionError();
  }
  if (!response.ok) throw new ApiError(response.status);
  let data: unknown;
  try { data = await response.json(); } catch { throw new ConnectionError(); }
  if (!isEvaluationResult(data)) throw new Error('服务返回的结果结构不完整，请检查服务日志。');
  return data;
}
