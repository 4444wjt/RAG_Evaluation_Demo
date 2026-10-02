export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type ResultRow = Record<string, JsonValue>;

/** Mirrors the existing FastAPI contract; no engine-only arguments. */
export interface EvaluateRequest {
  doc_path: string | null;
  testset_path: string | null;
  benchmark: 'truthful_qa' | null;
  benchmark_limit: number;
  model_names: string[] | null;
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
export type ResultSource = 'live' | 'demo';
