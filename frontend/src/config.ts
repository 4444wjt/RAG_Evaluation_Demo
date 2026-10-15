export const REPO_URL = 'https://gitee.com/kkbushishou/rag-hallucination-evaluator';

/** Public demo builds set VITE_READ_ONLY=1: the workbench shows snapshots only and never starts a paid evaluation. */
export const READ_ONLY = import.meta.env.VITE_READ_ONLY === '1';

/** The GitHub Pages fallback build also sets VITE_STATIC_SITE=1: there is no API behind it, so the workbench never probes one. */
export const STATIC_SITE = import.meta.env.VITE_STATIC_SITE === '1';

/** Validated with the dataviz palette checker on the #151b24 surface (dark, all pairs for the first three, adjacent for six):
 *  lightness band, chroma floor, CVD ΔE ≥ 9.4, normal-vision ΔE ≥ 19.3, ≥ 3:1 contrast. Brand gold and status colours stay out.
 *  Charts and the workspace model cards share these, so a model keeps one colour across the app. */
export const MODEL_COLORS = ['#3987e5', '#d95926', '#199e70', '#9085e9', '#d55181', '#c98500'];

/** DeepSeek 在页面上一律显示为 DeepSeek-V4.1-Flash。
 *  依据：DeepSeek 官方「模型 & 价格」页写明 deepseek-flash 的模型版本为 DeepSeek-V4.1-Flash（https://api-docs.deepseek.com/zh-cn/quick_start/pricing）；
 *  2026-09-10 更新日志：V4.1 Flash 上线，旧 V4 Flash 下线，旧模型名路由到 V4.1 Flash。
 *  本项目请求 deepseek-chat（旧别名）。有服务端返回记录的运行（25 题正式运行、外部基准、鲁棒性体检、修复验证）返回的都是 deepseek-flash。
 *  演示快照（2026-09-30）与金融失败案例（2026-09-28）没有记录返回名，是按官方路由说明推断为 V4.1-Flash 的；页面上的运行都在 2026-09-10 之后。 */
export const DEEPSEEK_VERSION = 'DeepSeek-V4.1-Flash';

/** 服务端返回的模型名 → 显示名。 */
export const RETURNED_MODEL_LABELS: Readonly<Record<string, string>> = { 'deepseek-flash': DEEPSEEK_VERSION };

/** Public model IDs only. Update these if the local backend names are overridden. */
export const MODELS = [
  { id: 'deepseek-chat', label: DEEPSEEK_VERSION, detail: '请求名 deepseek-chat（旧别名）', initials: 'DS' },
  { id: 'Pro/Qwen/Qwen2.5-7B-Instruct', label: 'Qwen2.5-7B', detail: '通义千问 7B 指令模型', initials: 'QW' },
  { id: 'glm-5.2-fast-preview', label: 'GLM-5.2-Fast', detail: '智谱 GLM · 阿里云百炼', initials: 'GL' },
] as const;

export const DATASETS = [
  { path: 'demo.txt', label: '默认示例文档' },
  { path: 'scenarios/deposit_insurance.txt', label: '存款保险' },
  { path: 'scenarios/information_disclosure.txt', label: '政府信息公开' },
] as const;

export const METRICS = [
  { key: 'faithfulness', label: '忠实度' },
  { key: 'context_precision', label: '上下文精度' },
  { key: 'answer_relevancy', label: '答案相关性' },
  { key: 'answer_correctness', label: '事实一致性' },
  { key: 'context_recall', label: '回答完整性' },
] as const;
