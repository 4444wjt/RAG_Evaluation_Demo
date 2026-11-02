import type { EvaluateRequest, EvaluationRecord, EvaluationResult, ResultSource } from './api/types';
import { BYOK, MODELS } from './config';
import type { ImportedInput } from './api/inputFiles';

export interface WorkspaceConfig {
  docPath: string;
  testsetMode: 'auto' | 'custom' | 'benchmark';
  testsetPath: string;
  benchmarkLimit: number;
  modelNames: string[];
  /** Empty/absent preserves self-judging; this model need not be one of the tested models. */
  judgeModelName?: string;
  useDefaultThresholds: boolean;
  faithThreshold: number | null;
  contextThreshold: number | null;
  docImport?: ImportedInput;
  testsetImport?: ImportedInput;
}

export const initialConfig: WorkspaceConfig = {
  docPath: 'demo.txt', testsetMode: 'auto', testsetPath: 'testset_example.csv',
  benchmarkLimit: 20, modelNames: BYOK ? [] : MODELS.map((model) => model.id),
  useDefaultThresholds: true, faithThreshold: 0.8, contextThreshold: 0.8,
};

export function toRequest(config: WorkspaceConfig): EvaluateRequest {
  const benchmarkLimit = config.testsetMode !== 'benchmark'
    && (!Number.isInteger(config.benchmarkLimit) || config.benchmarkLimit < 1)
    ? initialConfig.benchmarkLimit : config.benchmarkLimit;
  return {
    doc_path: config.docPath.trim() || null,
    testset_path: config.testsetMode === 'custom' ? config.testsetPath.trim() : null,
    benchmark: config.testsetMode === 'benchmark' ? 'truthful_qa' : null,
    benchmark_limit: benchmarkLimit,
    model_names: [...config.modelNames],
    ...(config.judgeModelName ? { judge_model_name: config.judgeModelName } : {}),
    faith_threshold: config.useDefaultThresholds ? null : config.faithThreshold,
    ctx_prec_threshold: config.useDefaultThresholds ? null : config.contextThreshold,
    version_label: null,
  };
}

export function validateConfig(config: WorkspaceConfig): string | null {
  if (BYOK && config.testsetMode === 'benchmark') return '云端未开放 TruthfulQA，请选择自动生成或上传自定义题集。';
  if (!config.modelNames.length) return '请至少选择一个模型。';
  if (config.testsetMode === 'custom' && !config.testsetPath.trim()) return '请先选择或导入自定义测试集。';
  if (config.testsetMode === 'benchmark' && (!Number.isInteger(config.benchmarkLimit) || config.benchmarkLimit < 1)) return '基准样本数量必须是正整数。';
  if (!config.useDefaultThresholds && [config.faithThreshold, config.contextThreshold].some((value) => value !== null && (!Number.isFinite(value) || value < 0 || value > 1))) return '门禁阈值必须介于 0 和 1 之间。';
  return null;
}

export type RunStatus = 'idle' | 'running' | 'completed' | 'failed' | 'unknown';
export interface RetainedRun {
  result: EvaluationResult;
  source: 'live' | 'history';
  submittedConfig: WorkspaceConfig | null;
  request: EvaluateRequest | null;
}
export interface RunState {
  status: RunStatus;
  result: EvaluationResult | null;
  source: ResultSource | null;
  error: string | null;
  submittedConfig: WorkspaceConfig | null;
  lastReal: RetainedRun | null;
}
export const initialRunState: RunState = { status: 'idle', result: null, source: null, error: null, submittedConfig: null, lastReal: null };
export type RunAction =
  | { type: 'start'; config: WorkspaceConfig }
  | { type: 'complete'; result: EvaluationResult }
  | { type: 'demo'; result: EvaluationResult }
  | { type: 'history'; record: EvaluationRecord }
  | { type: 'restore' }
  | { type: 'fail'; message: string; uncertain?: boolean };

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'start': return { ...state, status: 'running', error: null, submittedConfig: { ...action.config, modelNames: [...action.config.modelNames] } };
    case 'complete': return { ...state, status: 'completed', result: action.result, source: 'live', error: null,
      lastReal: { result: action.result, source: 'live', submittedConfig: state.submittedConfig, request: state.submittedConfig ? toRequest(state.submittedConfig) : null } };
    case 'demo': return { ...state, status: 'completed', result: action.result, source: 'demo', error: null, submittedConfig: null };
    case 'history': {
      const result = { ...action.record.result, record_id: action.record.id, saved_at: action.record.created_at, save_error: null };
      const submittedConfig = action.record.request ? fromRequest(action.record.request) : null;
      const lastReal: RetainedRun = { result, source: 'history', submittedConfig, request: action.record.request };
      return { status: 'completed', result, source: 'history', error: null, submittedConfig, lastReal };
    }
    case 'restore': return state.lastReal ? { ...state, ...state.lastReal, status: 'completed', error: null } : state;
    case 'fail': return { ...state, status: action.uncertain ? 'unknown' : 'failed', error: action.message };
  }
}

export function fromRequest(request: EvaluateRequest): WorkspaceConfig {
  return {
    docPath: request.doc_path ?? '', testsetMode: request.benchmark ? 'benchmark' : request.testset_path ? 'custom' : 'auto',
    testsetPath: request.testset_path ?? initialConfig.testsetPath, benchmarkLimit: request.benchmark_limit,
    modelNames: [...(request.model_names ?? [])], judgeModelName: request.judge_model_name || undefined,
    useDefaultThresholds: request.faith_threshold === null && request.ctx_prec_threshold === null,
    faithThreshold: request.faith_threshold,
    contextThreshold: request.ctx_prec_threshold,
  };
}
