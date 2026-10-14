import { READ_ONLY, STATIC_SITE } from '../config';

/** One model as GET /models reports it. The key value never leaves the backend; only whether it is set. */
export interface ModelEntry {
  name: string;
  displayName: string;
  baseUrl: string;
  apiKeyEnv: string;
  keyConfigured: boolean;
  source: 'builtin' | 'local';
}

/** What PUT /models/local accepts per model. There is deliberately no key field. */
export interface LocalModelInput {
  name: string;
  display_name: string;
  base_url: string;
  api_key_env: string;
}

export const MODEL_CONFIG_UNAVAILABLE = '公开演示不开放模型配置。本地运行后，可在这里登记自己的模型。';
export const MAX_LOCAL_MODELS = 20;
/** Same rules as backend/core/model_registry.py; the backend checks again on save. */
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const KEY_ENV_PATTERN = /^VERISCOPE_[A-Z0-9_]{1,48}_API_KEY$/;

/** VERISCOPE_<NAME>_API_KEY derived from a model name, as a starting suggestion only. */
export function suggestKeyEnv(name: string): string {
  const stem = name.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48);
  return stem ? `VERISCOPE_${stem}_API_KEY` : '';
}

export function validateLocalModel(model: LocalModelInput, takenNames: string[]): string | null {
  const name = model.name.trim();
  if (!NAME_PATTERN.test(name)) return '模型名只能含字母、数字和 . _ / -，且以字母或数字开头（例如 qwen2.5-7b）。';
  if (takenNames.includes(name)) return `模型名 ${name} 已存在。`;
  if (model.display_name.trim().length > 64) return '显示名不能超过 64 个字符。';
  let url: URL;
  try { url = new URL(model.base_url.trim()); } catch { return '接口地址必须是 http 或 https 地址，例如 http://127.0.0.1:11434/v1。'; }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return '接口地址必须是 http 或 https 地址，例如 http://127.0.0.1:11434/v1。';
  if (url.username || url.password || url.search || url.hash) return '接口地址不能包含用户名、密码、查询参数或片段；密钥请写在 .env 中。';
  if (!KEY_ENV_PATTERN.test(model.api_key_env.trim())) return '密钥变量名必须形如 VERISCOPE_<名称>_API_KEY（大写字母、数字、下划线）。';
  return null;
}

function isModelEntry(value: unknown): value is ModelEntry {
  if (typeof value !== 'object' || value === null) return false;
  const model = value as Record<string, unknown>;
  return ['name', 'displayName', 'baseUrl', 'apiKeyEnv'].every((key) => typeof model[key] === 'string')
    && typeof model.keyConfigured === 'boolean' && (model.source === 'builtin' || model.source === 'local');
}

async function readModels(response: Response, fallback: string): Promise<ModelEntry[]> {
  if (!response.ok) {
    let detail: unknown;
    try { detail = ((await response.json()) as { detail?: unknown }).detail; } catch { /* keep the generic message */ }
    const messages: Record<number, string> = {
      403: '模型登记只能在运行后端的本机上修改。',
      404: '当前后端未提供模型配置，请启动最新版本的本地服务。',
      423: MODEL_CONFIG_UNAVAILABLE,
    };
    // 422 messages are fixed, client-safe texts from the registry validator.
    throw new Error(response.status === 422 && typeof detail === 'string' ? detail : messages[response.status] ?? fallback);
  }
  let value: unknown;
  try { value = await response.json(); } catch { throw new Error('模型列表无法读取，请检查本地后端。'); }
  const models = (value as { models?: unknown } | null)?.models;
  if (!Array.isArray(models) || !models.every(isModelEntry)) throw new Error('模型列表结构不完整，请检查本地后端。');
  return models;
}

export async function fetchModels(signal?: AbortSignal): Promise<ModelEntry[]> {
  if (READ_ONLY || STATIC_SITE) throw new Error(MODEL_CONFIG_UNAVAILABLE);
  let response: Response;
  try { response = await fetch('/api/models', { signal }); } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error('无法连接本地后端，请确认服务已启动后重试。');
  }
  return readModels(response, '模型列表未能读取，请检查本地后端后重试。');
}

export async function saveLocalModels(models: LocalModelInput[]): Promise<ModelEntry[]> {
  if (READ_ONLY || STATIC_SITE) throw new Error(MODEL_CONFIG_UNAVAILABLE);
  let response: Response;
  try {
    response = await fetch('/api/models/local', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ models }),
    });
  } catch {
    throw new Error('无法连接本地后端，登记未保存。');
  }
  return readModels(response, '模型登记未能保存，请检查本地后端后重试。');
}

export function toInput(model: ModelEntry): LocalModelInput {
  return { name: model.name, display_name: model.displayName, base_url: model.baseUrl, api_key_env: model.apiKeyEnv };
}
