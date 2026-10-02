import { createHash } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, ConnectionError, checkHealth, evaluate, isEvaluationResult } from '../api/client';
import type { EvaluationResult } from '../api/types';
import fixture from '../fixtures/demo.json';
import fixtureText from '../fixtures/demo.json?raw';
import provenanceReadme from '../fixtures/README.md?raw';
import { initialConfig, initialRunState, runReducer, toRequest, validateConfig } from '../state';
import type { WorkspaceConfig } from '../state';
import { count, numberValue, percent, seconds } from '../utils/format';

const DEEPSEEK = 'deepseek-chat';
const QWEN = 'Pro/Qwen/Qwen2.5-7B-Instruct';
const GLM = 'glm-5.2-fast-preview';
const demoResult: EvaluationResult = fixture.result;
const resultFields = [
  'summary', 'summary_df', 'gate_passed', 'gate_message', 'chart_path',
  'html_path', 'model_dfs', 'total_elapsed_seconds',
];
const fetchMock = vi.fn<typeof fetch>();

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn(async () => body),
  };
}

function config(overrides: Partial<WorkspaceConfig> = {}): WorkspaceConfig {
  return { ...initialConfig, modelNames: [...initialConfig.modelNames], ...overrides };
}

function rawTrace(model: string, index: number): string {
  const trace = demoResult.model_dfs[model][index].hallucination_trace;
  if (typeof trace !== 'string') throw new Error('The existing fixture trace must remain a string.');
  return trace;
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockRejectedValue(new Error('Unexpected mocked request; no network is permitted.'));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('offline API contract', () => {
  it('requires all eight result fields and accepts nullable artifact paths without changing rows', () => {
    expect(Object.keys(demoResult).sort()).toEqual([...resultFields].sort());
    expect(isEvaluationResult(demoResult)).toBe(true);
    expect(isEvaluationResult({ ...demoResult, chart_path: null, html_path: null, total_elapsed_seconds: null })).toBe(true);
    for (const field of resultFields) {
      const missing: Record<string, unknown> = { ...demoResult };
      delete missing[field];
      expect(isEvaluationResult(missing), `Missing ${field}`).toBe(false);
    }
    expect(isEvaluationResult({ ...demoResult, summary: [null] })).toBe(false);
    expect(isEvaluationResult({ ...demoResult, model_dfs: { [DEEPSEEK]: [null] } })).toBe(false);
    expect(isEvaluationResult({ ...demoResult, total_elapsed_seconds: Infinity })).toBe(false);
  });

  it('passes HTTP 200 with gate false through unchanged and sends exactly one POST', async () => {
    const orderedRows = [demoResult.summary[2], demoResult.summary[0], demoResult.summary[1]];
    const payload: EvaluationResult = {
      ...demoResult,
      summary: orderedRows,
      summary_df: orderedRows,
      gate_passed: false,
      gate_message: '已保存的门禁结论',
    };
    const request = toRequest(config());
    fetchMock.mockResolvedValue(response(200, payload) as unknown as Response);

    const actual = await evaluate(request);

    expect(actual).toBe(payload);
    expect(actual.gate_passed).toBe(false);
    expect(actual.summary.map((row) => row.model)).toEqual([GLM, DEEPSEEK, QWEN]);
    expect(actual.summary[1].neg_faithfulness).toBeNull();
    expect(actual.summary[1].hallucination_rate).toBe(0);
    expect(actual.model_dfs[DEEPSEEK][0].contexts).toBe(demoResult.model_dfs[DEEPSEEK][0].contexts);
    expect(actual.model_dfs[QWEN][3].hallucination_trace).toBe(rawTrace(QWEN, 3));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/evaluate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    });
  });

  it.each([403, 404, 409, 422, 500])('shows a safe actionable error for HTTP %i without exposing vendor detail or retrying', async (status) => {
    const vendorText = 'VENDOR_PRIVATE_DETAIL simulated-provider-secret';
    const failed = response(status, { detail: vendorText });
    fetchMock.mockResolvedValue(failed as unknown as Response);

    const error = await evaluate(toRequest(config())).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(status);
    expect((error as Error).message).toMatch(/请/);
    expect((error as Error).message).not.toContain(vendorText);
    expect((error as Error).message).not.toContain('simulated-provider-secret');
    expect(failed.json).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('reports a transport failure as an unknown result and never retries the evaluation', async () => {
    fetchMock.mockRejectedValue(new TypeError('VENDOR_PRIVATE_DETAIL connection reset'));

    const error = await evaluate(toRequest(config())).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ConnectionError);
    expect((error as Error).message).toContain('评测结果未知');
    expect((error as Error).message).toContain('服务端可能仍在运行');
    expect((error as Error).message).not.toContain('VENDOR_PRIVATE_DETAIL');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('treats an unreadable successful response as uncertain without a second POST', async () => {
    const invalidJson = response(200, null);
    invalidJson.json.mockRejectedValue(new SyntaxError('VENDOR_PRIVATE_DETAIL malformed body'));
    fetchMock.mockResolvedValue(invalidJson as unknown as Response);

    await expect(evaluate(toRequest(config()))).rejects.toBeInstanceOf(ConnectionError);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects an incomplete successful envelope instead of fabricating result fields', async () => {
    fetchMock.mockResolvedValue(response(200, { summary: [], detail: 'VENDOR_PRIVATE_DETAIL' }) as unknown as Response);

    const error = await evaluate(toRequest(config())).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toContain('结果结构不完整');
    expect((error as Error).message).not.toContain('VENDOR_PRIVATE_DETAIL');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('checks only health and reports unavailable or malformed health responses as offline', async () => {
    fetchMock.mockResolvedValueOnce(response(200, { status: 'ok' }) as unknown as Response)
      .mockResolvedValueOnce(response(500, { detail: 'private' }) as unknown as Response)
      .mockResolvedValueOnce(response(200, { status: 'starting' }) as unknown as Response)
      .mockRejectedValueOnce(new TypeError('offline'));

    expect(await checkHealth()).toBe(true);
    expect(await checkHealth()).toBe(false);
    expect(await checkHealth()).toBe(false);
    expect(await checkHealth()).toBe(false);
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/health')).toBe(true);
  });
});

describe('request and workspace state', () => {
  it('uses the public API defaults and leaves default thresholds to the server', () => {
    expect(toRequest(config())).toEqual({
      doc_path: 'demo.txt', testset_path: null, benchmark: null, benchmark_limit: 20,
      model_names: [DEEPSEEK, QWEN, GLM], faith_threshold: null, ctx_prec_threshold: null, version_label: null,
    });
  });

  it('makes custom and benchmark test sources mutually exclusive while preserving selected model order', () => {
    const custom = toRequest(config({
      docPath: '  demo.txt  ', testsetMode: 'custom', testsetPath: '  testset_example.csv  ',
      modelNames: [GLM, DEEPSEEK],
    }));
    expect(custom).toMatchObject({
      doc_path: 'demo.txt', testset_path: 'testset_example.csv', benchmark: null, model_names: [GLM, DEEPSEEK],
    });
    const benchmark = toRequest(config({
      docPath: '   ', testsetMode: 'benchmark', testsetPath: 'ignored-custom.csv', benchmarkLimit: 7,
    }));
    expect(benchmark).toMatchObject({ doc_path: null, testset_path: null, benchmark: 'truthful_qa', benchmark_limit: 7 });
  });

  it.each([
    ['auto', NaN], ['auto', 0], ['auto', 1.5],
    ['custom', NaN], ['custom', 0], ['custom', 1.5],
  ] as const)('switching from an invalid cached benchmark count to %s sends the valid default for %s', (testsetMode, benchmarkLimit) => {
    const benchmarkConfig = config({ testsetMode: 'benchmark', benchmarkLimit });
    expect(validateConfig(benchmarkConfig)).not.toBeNull();
    const switched = { ...benchmarkConfig, testsetMode };
    expect(validateConfig(switched)).toBeNull();
    const request = toRequest(switched);
    expect(request.benchmark_limit).toBe(initialConfig.benchmarkLimit);
    expect(JSON.parse(JSON.stringify(request)).benchmark_limit).toBe(initialConfig.benchmarkLimit);
    expect(request.benchmark).toBeNull();
    expect(request.testset_path).toBe(testsetMode === 'custom' ? initialConfig.testsetPath : null);
    expect(switched.benchmarkLimit).toBe(benchmarkLimit);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('preserves valid cached counts in every mode and still rejects invalid benchmark counts', () => {
    for (const testsetMode of ['auto', 'custom', 'benchmark'] as const) {
      const valid = config({ testsetMode, benchmarkLimit: 7 });
      expect(validateConfig(valid)).toBeNull();
      expect(toRequest(valid).benchmark_limit).toBe(7);
    }
    for (const benchmarkLimit of [NaN, 0, 1.5]) {
      expect(validateConfig(config({ testsetMode: 'benchmark', benchmarkLimit }))).not.toBeNull();
    }
  });

  it('keeps explicit zero thresholds and rejects inputs that cannot form a valid request', () => {
    const explicit = config({ useDefaultThresholds: false, faithThreshold: 0, contextThreshold: 1 });
    expect(validateConfig(explicit)).toBeNull();
    expect(toRequest(explicit)).toMatchObject({ faith_threshold: 0, ctx_prec_threshold: 1 });
    expect(validateConfig(config({ modelNames: [] }))).not.toBeNull();
    expect(validateConfig(config({ testsetMode: 'custom', testsetPath: ' ' }))).not.toBeNull();
    expect(validateConfig(config({ testsetMode: 'benchmark', benchmarkLimit: 0 }))).not.toBeNull();
    expect(validateConfig(config({ testsetMode: 'benchmark', benchmarkLimit: 1.5 }))).not.toBeNull();
    expect(validateConfig(config({ useDefaultThresholds: false, faithThreshold: NaN }))).not.toBeNull();
    expect(validateConfig(config({ useDefaultThresholds: false, contextThreshold: 1.1 }))).not.toBeNull();
  });

  it('retains the last completed result on start and failure and freezes the submitted model selection', () => {
    const completed = runReducer(initialRunState, { type: 'complete', result: demoResult });
    const submitted = config({ modelNames: [DEEPSEEK] });
    const started = runReducer(completed, { type: 'start', config: submitted });
    submitted.modelNames.push(QWEN);
    expect(started).toMatchObject({ status: 'running', source: 'live', error: null });
    expect(started.result).toBe(demoResult);
    expect(started.submittedConfig?.modelNames).toEqual([DEEPSEEK]);
    expect(completed.status).toBe('completed');

    const failed = runReducer(started, { type: 'fail', message: '本地服务错误' });
    expect(failed.status).toBe('failed');
    expect(failed.result).toBe(demoResult);
    expect(failed.source).toBe('live');
    const uncertain = runReducer(started, { type: 'fail', message: '评测结果未知', uncertain: true });
    expect(uncertain.status).toBe('unknown');
    expect(uncertain.result).toBe(demoResult);
    expect(uncertain.submittedConfig).toBe(started.submittedConfig);
  });

  it('loads the existing demo state without invoking the evaluator', () => {
    const state = runReducer(initialRunState, { type: 'demo', result: demoResult });
    expect(state).toMatchObject({ status: 'completed', source: 'demo', error: null, submittedConfig: null });
    expect(state.result).toBe(demoResult);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('metric display and immutable demo evidence', () => {
  it('displays measured zero separately from missing, nonfinite, or string-valued metrics', () => {
    expect(numberValue(0)).toBe(0);
    expect(percent(0)).toBe('0.0%');
    expect(count(0)).toBe('0');
    expect(seconds(0)).toBe('0.0s');
    for (const missing of [null, undefined, NaN, Infinity, -Infinity, '0', '']) {
      expect(numberValue(missing)).toBeNull();
      expect(percent(missing)).toBe('—');
      expect(count(missing)).toBe('—');
      expect(seconds(missing)).toBe('—');
    }
  });

  it('preserves the actual run metrics and missing gate text without deriving a different conclusion', () => {
    expect(fixture.provenance.run).toMatchObject({ version: '20260930-1958', timestamp: '2026-09-30 19:58:34' });
    expect(demoResult.summary_df).toEqual(demoResult.summary);
    expect(demoResult.summary.map((row) => row.model)).toEqual([DEEPSEEK, QWEN, GLM]);
    expect(demoResult.gate_passed).toBe(true);
    expect(demoResult.gate_message).toBe('');
    expect(fixture.provenance.missing_fields).toEqual(['gate_message']);
    expect(demoResult.total_elapsed_seconds).toBe(518.73);
    expect(demoResult.summary[0]).toMatchObject({ faithfulness: 1, hallucination_rate: 0, neg_faithfulness: null, trace_statement_count: 5 });
    expect(demoResult.summary[1]).toMatchObject({ faithfulness: 0.25, score: 0.6666, hallucination_rate: 0.6667, hallucination_ratio: 0.6, trace_fabrication_count: 3 });
    expect(demoResult.summary[2]).toMatchObject({ trace_statement_count: 9, elapsed_seconds: 102.53 });
    expect(Object.values(demoResult.model_dfs).map((rows) => rows.length)).toEqual([6, 6, 6]);
  });

  it('preserves raw trace strings, existing evidence, empty statements, and per-case zero versus null', () => {
    expect(rawTrace(DEEPSEEK, 0)).toBe('{"statements": [{"text": "星云科技最新一轮B轮融资金额达5亿元人民币。", "verdict": "supported", "evidence": "星云科技已累计获得三轮融资，最新一轮B轮融资金额达5亿元人民币。"}]}');
    expect(rawTrace(DEEPSEEK, 3)).toBe('{"statements": []}');
    expect(rawTrace(QWEN, 3)).toBe('{"statements": [{"text": "文档中没有提及编号为「XH-2024」的金融行业审计批次的具体幻觉段落定位结果。", "verdict": "fabrication", "evidence": ""}]}');
    expect(demoResult.model_dfs[QWEN][0].faithfulness).toBe(0);
    expect(demoResult.model_dfs[GLM][0].faithfulness).toBeNull();
    expect(demoResult.model_dfs[DEEPSEEK][3]).toMatchObject({ faithfulness: null, context_precision: 0, case_type: 'negative' });
    expect(demoResult.model_dfs[DEEPSEEK][0].context_precision).toBe(0.9999999999);
    for (const rows of Object.values(demoResult.model_dfs)) {
      for (const row of rows) {
        expect(typeof row.contexts).toBe('string');
        expect(typeof row.hallucination_trace).toBe('string');
      }
    }
  });

  it('matches recorded source hashes and the frozen fixture checksum using only static fixture files', () => {
    const sources = fixture.provenance.source_hashes;
    expect(Object.keys(sources).sort()).toEqual([...fixture.provenance.files].sort());
    expect(fixture.provenance.files).toHaveLength(7);
    for (const [path, metadata] of Object.entries(sources)) {
      expect(path).not.toMatch(/^(?:[A-Za-z]:|\/|\.\.)/);
      expect(metadata.sha256).toMatch(/^[a-f0-9]{64}$/);
      expect(metadata.bytes).toBeGreaterThan(0);
      expect(provenanceReadme).toContain(`| \`${path}\` | ${metadata.bytes} | \`${metadata.sha256}\` |`);
    }
    expect(sources['multi_model_summary.csv'].sha256).toBe('e3690316ce8997a30691fed11a86a8ba6e749485261a7045404840fcb7c01db0');
    expect(provenanceReadme).toContain(createHash('sha256').update(fixtureText, 'utf8').digest('hex'));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
