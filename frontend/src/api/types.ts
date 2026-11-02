export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type ResultRow = Record<string, JsonValue>;

/** Mirrors the existing FastAPI contract; no engine-only arguments. */
export interface EvaluateRequest {
  doc_path: string | null;
  testset_path: string | null;
  benchmark: 'truthful_qa' | null;
  benchmark_limit: number;
  model_names: string[] | null;
  judge_model_name?: string | null;
  faith_threshold: number | null;
  ctx_prec_threshold: number | null;
  version_label: string | null;
}

export interface EvaluationResult {
  summary: ResultRow[];
  summary_df: ResultRow[];
  gate_passed: boolean;
  gate_message: string;
  chart_path: string | null;
  html_path: string | null;
  model_dfs: Record<string, ResultRow[]>;
  total_elapsed_seconds: number | null;
  faith_threshold?: number | null;
  ctx_prec_threshold?: number | null;
  record_id?: string | null;
  saved_at?: string | null;
  save_error?: string | null;
}

export interface EvaluationRecord {
  schema_version: 1;
  id: string;
  created_at: string;
  source: 'evaluation' | 'imported';
  request: EvaluateRequest | null;
  result: EvaluationResult;
  report_html: string | null;
  chart_png_base64: string | null;
  artifact_warnings: string[];
}

export interface EvaluationRecordSummary {
  id: string;
  created_at: string;
  source: 'evaluation' | 'imported';
  document_name: string | null;
  models: string[];
  gate_passed: boolean;
  total_elapsed_seconds: number | null;
  has_report: boolean;
  artifact_warnings: string[];
}

export interface EvaluationHistory {
  records: EvaluationRecordSummary[];
  unreadable_count: number;
}

export interface DemoFixture {
  result: EvaluationResult;
  provenance: {
    label: string;
    description: string;
    files: string[];
    [key: string]: unknown;
  };
}

export type View = 'overview' | 'workspace' | 'comparison' | 'trace' | 'report';
export type ResultSource = 'live' | 'demo' | 'history';
