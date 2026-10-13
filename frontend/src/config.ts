export const REPO_URL = 'https://github.com/4444wjt/RAG_Evaluation_Demo';

/** Public demo builds set VITE_READ_ONLY=1: the workbench shows snapshots only and never starts a paid evaluation. */
export const READ_ONLY = import.meta.env.VITE_READ_ONLY === '1';

/** The GitHub Pages fallback build also sets VITE_STATIC_SITE=1: there is no API behind it, so the workbench never probes one. */
export const STATIC_SITE = import.meta.env.VITE_STATIC_SITE === '1';

/** Validated with the dataviz palette checker on the #151b24 surface (dark, all pairs for the first three, adjacent for six):
 *  lightness band, chroma floor, CVD ΔE ≥ 9.4, normal-vision ΔE ≥ 19.3, ≥ 3:1 contrast. Brand gold and status colours stay out.
 *  Charts and the workspace model cards share these, so a model keeps one colour across the app. */
export const MODEL_COLORS = ['#3987e5', '#d95926', '#199e70', '#9085e9', '#d55181', '#c98500'];

/** Public model IDs only. Update these if the local backend names are overridden. */
export const MODELS = [
  { id: 'deepseek-chat', label: 'DeepSeek', detail: 'DeepSeek 通用对话模型', initials: 'DS' },
  { id: 'Pro/Qwen/Qwen2.5-7B-Instruct', label: 'Qwen2.5-7B', detail: '通义千问 7B 指令模型', initials: 'QW' },
  { id: 'glm-5.2-fast-preview', label: 'GLM-5.2-Fast', detail: '智谱 GLM · 阿里云百炼', initials: 'GL' },
] as const;

export const DATASETS = [
  { path: 'demo.txt', label: '默认示例文档' },
  { path: 'scenarios/deposit_insurance.txt', label: '存款保险' },
  { path: 'scenarios/information_disclosure.txt', label: '信息披露' },
] as const;

export const METRICS = [
  { key: 'faithfulness', label: '忠实度' },
  { key: 'context_precision', label: '上下文精度' },
  { key: 'answer_relevancy', label: '答案相关性' },
  { key: 'answer_correctness', label: '事实一致性' },
  { key: 'context_recall', label: '回答完整性' },
] as const;
