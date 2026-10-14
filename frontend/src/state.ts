import type { EvaluateRequest, EvaluationResult, ResultSource } from './api/types';
import { MODELS } from './config';
import type { ImportedInput } from './api/inputFiles';

export interface WorkspaceConfig {
  docPath: string;
  testsetMode: 'auto' | 'custom' | 'benchmark';
  testsetPath: string;
  benchmarkLimit: number;
  modelNames: string[];
  useDefaultThresholds: boolean;
  faithThreshold: number;
  contextThreshold: number;
  docImport?: ImportedInput;
  testsetImport?: ImportedInput;
}

export const initialConfig: WorkspaceConfig = {
  docPath: 'demo.txt', testsetMode: 'auto', testsetPath: 'testset_example.csv',
  benchmarkLimit: 20, modelNames: MODELS.map((model) => model.id),
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
    faith_threshold: config.useDefaultThresholds ? null : config.faithThreshold,
    ctx_prec_threshold: config.useDefaultThresholds ? null : config.contextThreshold,
    version_label: null,
  };
}

export function validateConfig(config: WorkspaceConfig): string | null {
  if (!config.modelNames.length) return '请至少选择一个模型。';
  if (config.testsetMode === 'custom' && !config.testsetPath.trim()) return '请先选择或导入自定义测试集。';
  if (config.testsetMode === 'benchmark' && (!Number.isInteger(config.benchmarkLimit) || config.benchmarkLimit < 1)) return '基准样本数量必须是正整数。';
  if (!config.useDefaultThresholds && [config.faithThreshold, config.contextThreshold].some((value) => !Number.isFinite(value) || value < 0 || value > 1)) return '门禁阈值必须介于 0 和 1 之间。';
  return null;
}

export type RunStatus = 'idle' | 'running' | 'completed' | 'failed' | 'unknown';
export interface RunState {
  status: RunStatus;
  result: EvaluationResult | null;
  source: ResultSource | null;
  error: string | null;
  submittedConfig: WorkspaceConfig | null;
}
export const initialRunState: RunState = { status: 'idle', result: null, source: null, error: null, submittedConfig: null };
export type RunAction =
  | { type: 'start'; config: WorkspaceConfig }
  | { type: 'complete'; result: EvaluationResult }
  | { type: 'demo'; result: EvaluationResult }
  | { type: 'fail'; message: string; uncertain?: boolean };

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'start': return { ...state, status: 'running', error: null, submittedConfig: { ...action.config, modelNames: [...action.config.modelNames] } };
    case 'complete': return { ...state, status: 'completed', result: action.result, source: 'live', error: null };
    case 'demo': return { ...state, status: 'completed', result: action.result, source: 'demo', error: null, submittedConfig: null };
    case 'fail': return { ...state, status: action.uncertain ? 'unknown' : 'failed', error: action.message };
  }
}
