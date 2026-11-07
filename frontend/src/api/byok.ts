import type { ModelEntry } from './models';

export interface ByokModel { name: string; base_url: string; api_key: string }
export const MAX_BYOK_MODELS = 3;
export const BYOK_PROVIDERS = [
  { label: 'DeepSeek', base_url: 'https://api.deepseek.com', name: 'deepseek-chat' },
  { label: '阿里云百炼', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', name: 'qwen-flash' },
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', name: '' },
  { label: '硅基流动', base_url: 'https://api.siliconflow.cn/v1', name: '' },
  { label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', name: '' },
  { label: 'Moonshot', base_url: 'https://api.moonshot.cn/v1', name: '' },
] as const;

// Never expose this collection to config, history, URL or storage. Only request construction reads keys.
let models: ByokModel[] = [];
export const byokEntries = (): ModelEntry[] => models.map((model) => ({ name: model.name, displayName: model.name,
  baseUrl: model.base_url, apiKeyEnv: '', keyConfigured: true, source: 'local' }));

export function validateByokModel(model: ByokModel, taken: string[]): string | null {
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/.test(model.name.trim())) return '请填写有效模型名，只能含字母、数字和 . _ / -。';
  if (taken.includes(model.name.trim())) return '模型名已存在，请编辑已有配置或使用不同模型名。';
  let url: URL;
  try { url = new URL(model.base_url.trim()); } catch { return '请填写完整的 HTTPS OpenAI 兼容接口地址。'; }
  if (model.base_url.trim().length > 512) return '接口地址不能超过 512 个字符。';
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash) return '接口须使用 HTTPS，且不能包含账户、密钥、查询参数或片段。';
  if (!/^[\x21-\x7e]{8,4096}$/.test(model.api_key.trim())) return '请填写有效 API Key（8 至 4096 个非空白 ASCII 字符）。';
  return null;
}

export function saveByokModel(model: ByokModel, editing?: string): ModelEntry[] {
  if (!editing && models.length >= MAX_BYOK_MODELS) throw new Error('最多配置 3 个模型（含独立裁判）。');
  const problem = validateByokModel(model, models.filter((entry) => entry.name !== editing).map((entry) => entry.name));
  if (problem) throw new Error(problem);
  const clean = { name: model.name.trim(), base_url: model.base_url.trim().replace(/\/$/, ''), api_key: model.api_key.trim() };
  models = editing ? models.map((entry) => entry.name === editing ? clean : entry) : [...models, clean];
  return byokEntries();
}

export function removeByokModel(name: string): ModelEntry[] { models = models.filter((model) => model.name !== name); return byokEntries(); }

export function byokRequestModels(names: string[], judge?: string | null): ByokModel[] {
  const needed = [...new Set([...names, ...(judge ? [judge] : [])])];
  if (!needed.length || needed.length > MAX_BYOK_MODELS) throw new Error('请选择 1 至 3 个已配置模型（含独立裁判）。');
  return needed.map((name) => {
    const model = models.find((entry) => entry.name === name);
    if (!model) throw new Error('请先在模型配置中填写本次使用的模型和 API Key。刷新页面后需要重新填写。');
    return { ...model };
  });
}

/** Mask credentials even if a provider response happens to echo one. */
export function safeByokMessage(value: string): string {
  return models.reduce((text, model) => text.split(model.api_key).join('[密钥已隐藏]'), value).slice(0, 500);
}
