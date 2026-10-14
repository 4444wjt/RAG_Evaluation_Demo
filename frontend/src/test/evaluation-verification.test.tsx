import { webcrypto, createHash } from 'node:crypto';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EvaluationVerificationView, validateReviewBundle, verifyReviewBinding } from '../features/EvaluationVerificationView';
import type { ReviewBundle } from '../features/EvaluationVerificationView';
import type { EvaluationResult } from '../api/types';
import demo from '../fixtures/demo.json';
import raw from '../fixtures/demo.json?raw';
import reviews from '../fixtures/credibility-review.json';
import failureCase from '../fixtures/financial-failure-case.json';

const result = () => structuredClone(demo.result) as EvaluationResult;
// Isolated pending fixtures allow the published package to receive real reviews later.
const bundle = (): ReviewBundle => {
  const value = structuredClone(reviews) as ReviewBundle;
  for (const record of value.records) Object.assign(record, { status: 'pending', reviewer: null, reviewedAt: null, answerReview: null, statementReviews: [] });
  for (const check of [value.gateRuleReview, value.reproduction.snapshotReplay, value.reproduction.modelRerun]) Object.assign(check, { status: 'unverified', reviewer: null, reviewedAt: null, note: null });
  return value;
};
const open = async (reviewBundle: unknown = bundle()) => {
  const view = render(<EvaluationVerificationView result={result()} source="demo" reviewBundle={reviewBundle}/>);
  await screen.findByRole('table');
  return view;
};

// Completed reviews below are synthetic test data, never published review conclusions.
function reviewed(status: 'agree' | 'disagree' | 'uncertain' = 'agree'): ReviewBundle {
  const value = bundle();
  const record = value.records[0];
  const trace = JSON.parse(demo.result.model_dfs['deepseek-chat'][0].hallucination_trace);
  record.status = status;
  record.reviewer = { name: '测试复核者', role: 'author' };
  record.reviewedAt = '2026-10-04';
  record.answerReview = { extraction: 'complete', refusal: 'not_applicable', reason: '测试人工理由' };
  record.statementReviews = [{ statementIndex: 0, humanVerdict: status === 'uncertain' ? 'uncertain' : status === 'disagree' ? 'fabrication' : 'supported', reason: '测试逐句理由', evidence: trace.statements[0].evidence }];
  return value;
}

beforeEach(() => vi.stubGlobal('crypto', webcrypto));

describe('Frozen audit package and binding', () => {
  it('selects exactly 13 answers, including six empty traces and twelve original statements', () => {
    expect(validateReviewBundle(reviews)).toBe(true);
    expect(reviews.records).toHaveLength(13);
    let empty = 0;
    let statements = 0;
    let negative = 0;
    for (const record of reviews.records) {
      const row = demo.result.model_dfs[record.modelId as keyof typeof demo.result.model_dfs][record.rowIndex];
      const count = JSON.parse(row.hallucination_trace).statements.length;
      statements += count;
      if (!count) empty++;
      if (row.case_type === 'negative') negative++;
    }
    expect({ empty, statements, negative }).toEqual({ empty: 6, statements: 12, negative: 9 });
    expect(createHash('sha256').update(raw, 'utf8').digest('hex')).toBe(reviews.snapshotSha256);
  });

  it('binds only identical frozen content and accepts object property reordering', async () => {
    expect((await verifyReviewBinding(reviews, result())).status).toBe('ready');
    const reversed = Object.fromEntries(Object.entries(result()).reverse()) as unknown as EvaluationResult;
    expect((await verifyReviewBinding(reviews, reversed)).status).toBe('ready');
    const changed = result();
    changed.model_dfs['deepseek-chat'][0].answer = '另一条回答';
    expect((await verifyReviewBinding(reviews, changed)).status).toBe('mismatch');
    changed.model_dfs = result().model_dfs;
    changed.summary_df[0].faithfulness = 0;
    expect((await verifyReviewBinding(reviews, changed)).status).toBe('mismatch');
  });

  it('rejects altered snapshot hashes and versions', async () => {
    const value = bundle();
    value.snapshotSha256 = '0'.repeat(64);
    expect((await verifyReviewBinding(value, result())).status).toBe('mismatch');
    value.snapshotVersion = 'another-run';
    expect((await verifyReviewBinding(value, result())).status).toBe('mismatch');
  });

  it('fails closed when crypto is missing or rejects', async () => {
    vi.stubGlobal('crypto', {});
    expect((await verifyReviewBinding(reviews, result())).status).toBe('unavailable');
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockRejectedValue(new Error('unavailable')) } });
    expect((await verifyReviewBinding(reviews, result())).status).toBe('unavailable');
  });

  it.each(['duplicate', 'out-of-range', 'wrong-selection', 'missing', 'invented-review', 'bad-schema'])('rejects invalid references or metadata: %s', mode => {
    const value = bundle();
    if (mode === 'duplicate') value.records[1] = structuredClone(value.records[0]);
    if (mode === 'out-of-range') value.records[0].rowIndex = 99;
    if (mode === 'wrong-selection') value.records[0].rowIndex = 1;
    if (mode === 'missing') value.records.pop();
    if (mode === 'invented-review') value.records[0].reviewer = { name: '不应出现的人员', role: 'author' };
    if (mode === 'bad-schema') (value as unknown as { schemaVersion: number }).schemaVersion = 2;
    expect(validateReviewBundle(value)).toBe(false);
  });

  it.each(['reviewer', 'date', 'reason', 'statement', 'invalid-date', 'duplicate-statement', 'wrong-index', 'negative-index'])('rejects incomplete completed reviews: %s', mode => {
    const value = reviewed();
    const record = value.records[0];
    if (mode === 'reviewer') record.reviewer = null;
    if (mode === 'date') record.reviewedAt = null;
    if (mode === 'reason') record.answerReview!.reason = '';
    if (mode === 'statement') record.statementReviews = [];
    if (mode === 'invalid-date') record.reviewedAt = '2026-02-30';
    if (mode === 'duplicate-statement') record.statementReviews.push(record.statementReviews[0]);
    if (mode === 'wrong-index') record.statementReviews[0].statementIndex = 99;
    if (mode === 'negative-index') record.statementReviews[0].statementIndex = -1;
    expect(validateReviewBundle(value)).toBe(false);
  });

  it.each(['missing', 'extraction-uncertain', 'questionable', 'refusal-uncertain', 'different-verdict', 'statement-uncertain'])('rejects contradictions with agreement: %s', mode => {
    const value = reviewed();
    const record = value.records[0];
    if (mode === 'missing') record.answerReview!.extraction = 'missing';
    if (mode === 'extraction-uncertain') record.answerReview!.extraction = 'uncertain';
    if (mode === 'questionable') record.answerReview!.refusal = 'questionable';
    if (mode === 'refusal-uncertain') record.answerReview!.refusal = 'uncertain';
    if (mode === 'different-verdict') record.statementReviews[0].humanVerdict = 'fact_error';
    if (mode === 'statement-uncertain') record.statementReviews[0].humanVerdict = 'uncertain';
    expect(validateReviewBundle(value)).toBe(false);
  });

  it('preserves manual disagreement about evidence even when statement labels match', async () => {
    const value = reviewed('disagree');
    value.records[0].statementReviews[0].humanVerdict = 'supported';
    value.records[0].answerReview!.reason = '测试：结论标签相同，但对引用依据存在分歧。';
    expect((await verifyReviewBinding(value, result())).status).toBe('ready');
    expect(value.records[0].status).toBe('disagree');
  });

  it('accepts recorded extraction omissions and unresolved items without inventing empty-trace statements', async () => {
    const value = bundle();
    const record = value.records.find(entry => entry.modelId === 'Pro/Qwen/Qwen2.5-7B-Instruct' && entry.rowIndex === 2)!;
    Object.assign(record, {
      status: 'disagree', reviewer: { name: '测试复核者', role: 'author' }, reviewedAt: '2026-10-04',
      answerReview: { extraction: 'missing', refusal: 'uncertain', reason: '测试：发现抽取遗漏；拒答处理依据不足，仍待确认。' },
    });
    expect((await verifyReviewBinding(value, result())).status).toBe('ready');
    expect(record.statementReviews).toEqual([]);
    await open(value);
    await userEvent.click(screen.getByRole('button', { name: '查看Qwen2.5-7B用例 3 的核验详情' }));
    const detail = document.getElementById('verification-detail-1-2')!;
    expect(within(detail).getByText('抽取判断')).toBeVisible();
    expect(within(detail).getByText('存在遗漏')).toBeVisible();
    expect(within(detail).getByText('拒答处理')).toBeVisible();
    expect(within(detail).getByText('无法确认')).toBeVisible();
    expect(within(detail).queryByText('陈述 1')).not.toBeInTheDocument();
  });

  it('requires a real check record before claiming rule review or reproduction', () => {
    const value = bundle();
    value.gateRuleReview.status = 'verified';
    expect(validateReviewBundle(value)).toBe(false);
    value.gateRuleReview = { status: 'verified', reviewer: { name: '测试人员', role: 'author' }, reviewedAt: '2026-10-04', note: '测试核对记录' };
    expect(validateReviewBundle(value)).toBe(true);
  });
});

describe('Read-only verification page', () => {
  it('shows pending coverage with no completed conclusions or invented identities', async () => {
    await open();
    const scope = screen.getByRole('region', { name: '人工核验范围' });
    expect(scope).toHaveTextContent('已检查0条待核验13条未纳入5条');
    expect(scope).toHaveTextContent('预选 13/18 条回答，关联 12/19 条逐句记录');
    expect(screen.getAllByText('待核验', { selector: '.verification-status' })).toHaveLength(13);
    expect(screen.getAllByText('未验证', { exact: true })).toHaveLength(2);
    expect(screen.getByText('尚未人工复核')).toBeInTheDocument();
    expect(screen.queryByText('PASS', { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText('独立复核', { exact: true })).not.toBeInTheDocument();
  });

  it('filters records and expands empty traces without inventing statements', async () => {
    await open();
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'Pro/Qwen/Qwen2.5-7B-Instruct');
    expect(document.querySelectorAll('[data-review-record]')).toHaveLength(4);
    await userEvent.click(screen.getByRole('button', { name: '查看Qwen2.5-7B用例 3 的核验详情' }));
    expect(screen.getByText('原记录没有逐句判定，需核验抽取完整性与拒答处理。')).toBeVisible();
    const detail = document.getElementById('verification-detail-1-2')!;
    expect(within(detail).queryByText('陈述 1')).not.toBeInTheDocument();
    expect(within(detail).getByText('尚未录入人工结论。')).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText('核验状态'), 'agree');
    expect(screen.getByText('没有符合筛选条件的核验记录。')).toBeVisible();
  });

  it('shows the archived financial FAIL separately without changing demo or review coverage', async () => {
    const original = JSON.stringify({ demo, reviews, failureCase });
    await open();
    const section = screen.getByRole('region', { name: '门禁拦截验证' });
    expect(section).toHaveTextContent('20260928-1313');
    expect(section).toHaveTextContent('FAIL');
    expect(within(section).getByText('以下是一次被质量门禁真实拦截的历史运行：指标未达门槛时，门禁拒绝放行。展示它是为了证明门禁的拦截能力，而非当前快照的评测结论。')).toBeVisible();
    await userEvent.click(within(section).getByText('查看问题、指标与门禁依据'));
    expect(within(section).getByRole('region', { name: '金融场景原始问题' }).querySelectorAll('article')).toHaveLength(3);
    for (const { row } of failureCase.positiveRows) {
      expect(within(section).getByRole('heading', { name: row.question })).toBeVisible();
      expect(within(section).getByText(row.answer)).toBeVisible();
    }
    const second = section.querySelectorAll('.verification-failure-questions article')[1];
    const context = second.querySelector('details')!;
    expect(context.open).toBe(false);
    expect([...second.children].map(element => element.tagName)).toEqual(['H4', 'P', 'P', 'DETAILS']);
    const gate = within(section).getByRole('region', { name: '金融场景原始门禁' });
    expect(gate).toHaveTextContent('正向忠实度100.0%');
    expect(gate).toHaveTextContent('正向上下文精度77.8%');
    expect(gate).toHaveTextContent('门槛 ≥ 80.0%');
    expect(gate).toHaveTextContent('不表示人工核验触发了拦截');
    expect(gate).toHaveTextContent('第二个问题的上下文精度为 33.3%');
    expect(gate).toHaveTextContent('归档正向均值 0.7778 低于 0.8 门槛');
    expect(gate).toHaveTextContent('低上下文精度不等于已确认的回答事实错误');
    await userEvent.click(within(section).getAllByText('查看参考答案与检索上下文')[1]);
    expect(within(section).getByText(failureCase.positiveRows[1].row.answer)).toBeVisible();
    const contexts = section.querySelectorAll('.verification-failure-questions article')[1].querySelectorAll('.verification-original')[2];
    expect(contexts).toBeVisible();
    expect(contexts.textContent).toBe(failureCase.positiveRows[1].row.contexts);
    expect(screen.getByRole('region', { name: '人工核验范围' })).toHaveTextContent('已检查0条待核验13条未纳入5条');
    expect(document.querySelectorAll('[data-review-record]')).toHaveLength(13);
    expect(JSON.stringify({ demo, reviews, failureCase })).toBe(original);
  });

  it.each(['agree', 'disagree', 'uncertain'] as const)('renders actual supplied review state %s without changing machine records', async status => {
    const original = JSON.stringify(demo);
    await open(reviewed(status));
    await userEvent.selectOptions(screen.getByLabelText('核验状态'), status);
    expect(document.querySelectorAll('[data-review-record]')).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: '查看DeepSeek-V4.1-Flash用例 1 的核验详情' }));
    expect(screen.getByText('测试人工理由')).toBeVisible();
    expect(screen.getByText(/作者自审/)).toBeVisible();
    expect(screen.getByText('机器原判定：有依据')).toBeVisible();
    expect(JSON.stringify(demo)).toBe(original);
  });

  it('counts all three checked states while retaining pending records and explicit manual conclusions', async () => {
    const value = reviewed('agree');
    for (const [index, status] of [[1, 'disagree'], [2, 'uncertain']] as const) {
      const record = value.records[index];
      const trace = JSON.parse(demo.result.model_dfs[record.modelId as keyof typeof demo.result.model_dfs][record.rowIndex].hallucination_trace);
      Object.assign(record, { status, reviewer: { name: '测试独立复核者', role: 'independent' }, reviewedAt: '2026-10-04', answerReview: { extraction: 'uncertain', refusal: 'uncertain', reason: '测试：存在未决项，保留人工汇总状态。' } });
      record.statementReviews = trace.statements.map((_: unknown, statementIndex: number) => ({ statementIndex, humanVerdict: 'uncertain', reason: '测试：证据不足。', evidence: null }));
    }
    await open(value);
    const scope = screen.getByRole('region', { name: '人工核验范围' });
    expect(scope).toHaveTextContent('已检查3条待核验10条未纳入5条');
    expect(scope).toHaveTextContent('已检查包含“无法确认”');
    for (const status of ['agree', 'disagree', 'uncertain', 'pending']) {
      await userEvent.selectOptions(screen.getByLabelText('核验状态'), status);
      expect(document.querySelectorAll('[data-review-record]')).toHaveLength(status === 'pending' ? 10 : 1);
    }
    await userEvent.selectOptions(screen.getByLabelText('核验状态'), 'uncertain');
    await userEvent.click(screen.getByRole('button', { name: '查看DeepSeek-V4.1-Flash用例 4 的核验详情' }));
    expect(screen.getByText('测试独立复核者')).toBeVisible();
    expect(screen.getByText('身份')).toBeVisible();
    expect(screen.getByText('独立复核')).toBeVisible();
    expect(screen.queryByText(/可信度评分/)).not.toBeInTheDocument();
    expect([...scope.querySelectorAll('dt')].map(element => element.textContent)).toEqual(['已检查', '待核验', '未纳入']);
  });

  it('distinguishes no result and live result even when the live payload matches the demo', () => {
    const view = render(<EvaluationVerificationView result={null} source={null}/>);
    expect(screen.getByText('尚未加载可核验结果')).toBeVisible();
    view.rerender(<EvaluationVerificationView result={result()} source="live"/>);
    expect(screen.getByText('本次结果尚无对应人工核验材料')).toBeVisible();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it.each(['invalid', 'mismatch', 'unavailable'])('hides all review statistics when %s', async mode => {
    const value = bundle();
    if (mode === 'invalid') value.records.pop();
    if (mode === 'mismatch') value.snapshotSha256 = '0'.repeat(64);
    if (mode === 'unavailable') vi.stubGlobal('crypto', {});
    render(<EvaluationVerificationView result={result()} source="demo" reviewBundle={value}/>);
    await screen.findByText(mode === 'invalid' ? '核验材料格式无效' : mode === 'mismatch' ? '核验材料与当前结果不对应' : '无法确认材料对应关系');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '人工核验范围' })).not.toBeInTheDocument();
  });

  it('does not flash old statistics on changed props or publish a stale digest response', async () => {
    let finish!: (digest: ArrayBuffer) => void;
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => new Promise<ArrayBuffer>(resolve => { finish = resolve; })) } });
    const view = render(<EvaluationVerificationView result={result()} source="demo"/>);
    expect(screen.getByText('正在确认核验材料对应关系…')).toBeVisible();
    view.rerender(<EvaluationVerificationView result={result()} source="live"/>);
    const bytes = Uint8Array.from(createHash('sha256').update(raw).digest()).buffer;
    await act(async () => finish(bytes));
    expect(screen.getByText('本次结果尚无对应人工核验材料')).toBeVisible();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('preserves all source and audit objects during binding and filtering', async () => {
    const payload = result();
    const review = bundle();
    const original = JSON.stringify({ payload, review });
    render(<EvaluationVerificationView result={payload} source="demo" reviewBundle={review}/>);
    await screen.findByRole('table');
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'glm-5.2-fast-preview');
    await userEvent.click(screen.getByRole('button', { name: '查看GLM-5.2-Fast用例 5 的核验详情' }));
    await waitFor(() => expect(screen.getByText('尚未录入人工结论。')).toBeVisible());
    expect(JSON.stringify({ payload, review })).toBe(original);
  });
});
