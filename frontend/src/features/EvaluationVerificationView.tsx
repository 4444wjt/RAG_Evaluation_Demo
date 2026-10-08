import { useEffect, useState } from 'react';
import type { DemoFixture, EvaluationResult, ResultSource } from '../api/types';
import { Icon } from '../components/Icon';
import { modelName, percent, textValue } from '../utils/format';
import demoRaw from '../fixtures/demo.json?raw';
import reviewData from '../fixtures/credibility-review.json';
import failureCase from '../fixtures/financial-failure-case.json';
import './evaluation-verification.css';

type ReviewStatus = 'pending' | 'agree' | 'disagree' | 'uncertain';
type Reviewer = { name: string; role: 'author' | 'independent' };
type Check = { status: 'unverified' | 'verified' | 'uncertain'; reviewer: Reviewer | null; reviewedAt: string | null; note: string | null };
type AnswerReview = {
  extraction: 'complete' | 'missing' | 'not_applicable' | 'uncertain';
  refusal: 'reasonable' | 'questionable' | 'not_applicable' | 'uncertain';
  reason: string;
};
type StatementReview = { statementIndex: number; humanVerdict: 'supported' | 'fact_error' | 'fabrication' | 'not_applicable' | 'uncertain'; reason: string; evidence: string | null };
export type ReviewRecord = {
  modelId: string; rowIndex: number; focus: string; status: ReviewStatus;
  reviewer: Reviewer | null; reviewedAt: string | null; answerReview: AnswerReview | null; statementReviews: StatementReview[];
};
export type ReviewBundle = {
  schemaVersion: 1; snapshotVersion: string; snapshotSha256: string;
  gateRuleReview: Check; reproduction: { snapshotReplay: Check; modelRerun: Check }; records: ReviewRecord[];
};
type Statement = { text: string; verdict: string; evidence: string };
const frozen = JSON.parse(demoRaw) as DemoFixture;
const targets: Record<string, number[]> = {
  'deepseek-chat': [0, 2, 3, 4, 5],
  'Pro/Qwen/Qwen2.5-7B-Instruct': [2, 3, 4, 5],
  'glm-5.2-fast-preview': [1, 3, 4, 5],
};
const statusLabels: Record<ReviewStatus, string> = { pending: '待核验', agree: '一致', disagree: '不一致', uncertain: '无法确认' };
const verdictLabels: Record<string, string> = { supported: '有依据', fact_error: '事实错误', fabrication: '无依据编造', not_applicable: '不适用', uncertain: '无法确认' };
const extractionLabels = { complete: '抽取完整', missing: '存在遗漏', not_applicable: '不适用', uncertain: '无法确认' };
const refusalLabels = { reasonable: '处理合理', questionable: '需要复查', not_applicable: '不适用', uncertain: '无法确认' };
const displayReviewText = (value: string): string => value.replaceAll('分歧', '不一致');
const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === 'string' && Boolean(value.trim());
const date = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const reviewer = (value: unknown): value is Reviewer => object(value) && nonempty(value.name) && ['author', 'independent'].includes(String(value.role));

function statements(modelId: string, rowIndex: number): Statement[] {
  const raw = frozen.result.model_dfs[modelId]?.[rowIndex]?.hallucination_trace;
  const trace = typeof raw === 'string' ? JSON.parse(raw) : raw;
  return object(trace) && Array.isArray(trace.statements) ? trace.statements as Statement[] : [];
}

function validCheck(value: unknown): value is Check {
  if (!object(value)) return false;
  if (value.status === 'unverified') return value.reviewer === null && value.reviewedAt === null && value.note === null;
  return ['verified', 'uncertain'].includes(String(value.status)) && reviewer(value.reviewer) && date(value.reviewedAt) && nonempty(value.note);
}

/** Checks audit references and explicit contradictions; never judges answer correctness. */
export function validateReviewBundle(value: unknown): value is ReviewBundle {
  if (!object(value) || value.schemaVersion !== 1 || !nonempty(value.snapshotVersion)
    || typeof value.snapshotSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.snapshotSha256)
    || !validCheck(value.gateRuleReview) || !object(value.reproduction)
    || !validCheck(value.reproduction.snapshotReplay) || !validCheck(value.reproduction.modelRerun)
    || !Array.isArray(value.records) || value.records.length !== 13) return false;
  const seen = new Set<string>();
  for (const record of value.records) {
    if (!object(record) || typeof record.modelId !== 'string' || typeof record.rowIndex !== 'number'
      || !Number.isInteger(record.rowIndex) || !Object.hasOwn(targets, record.modelId) || !targets[record.modelId].includes(record.rowIndex)
      || !nonempty(record.focus) || !['pending', 'agree', 'disagree', 'uncertain'].includes(String(record.status))
      || !Array.isArray(record.statementReviews)) return false;
    const key = `${record.modelId}:${record.rowIndex}`;
    if (seen.has(key)) return false;
    seen.add(key);
    if (record.status === 'pending') {
      if (record.reviewer !== null || record.reviewedAt !== null || record.answerReview !== null || record.statementReviews.length) return false;
      continue;
    }
    if (!reviewer(record.reviewer) || !date(record.reviewedAt) || !object(record.answerReview)
      || !['complete', 'missing', 'not_applicable', 'uncertain'].includes(String(record.answerReview.extraction))
      || !['reasonable', 'questionable', 'not_applicable', 'uncertain'].includes(String(record.answerReview.refusal))
      || !nonempty(record.answerReview.reason)) return false;
    if (record.status === 'agree' && (['missing', 'uncertain'].includes(String(record.answerReview.extraction))
      || ['questionable', 'uncertain'].includes(String(record.answerReview.refusal)))) return false;
    const originals = statements(record.modelId, record.rowIndex);
    if (record.statementReviews.length !== originals.length) return false;
    const indices = new Set<number>();
    for (const entry of record.statementReviews) {
      if (!object(entry) || typeof entry.statementIndex !== 'number' || !Number.isInteger(entry.statementIndex)
        || !originals[entry.statementIndex] || indices.has(entry.statementIndex)
        || !['supported', 'fact_error', 'fabrication', 'not_applicable', 'uncertain'].includes(String(entry.humanVerdict))
        || !nonempty(entry.reason) || !(entry.evidence === null || nonempty(entry.evidence))) return false;
      if (record.status === 'agree' && (entry.humanVerdict === 'uncertain'
        || entry.humanVerdict !== originals[entry.statementIndex].verdict)) return false;
      indices.add(entry.statementIndex);
    }
  }
  return true;
}

function sameData(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((item, index) => sameData(item, right[index]));
  if (object(left) && object(right)) {
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && sameData(left[key], right[key]));
  }
  return false;
}

export type Binding = { status: 'ready'; bundle: ReviewBundle } | { status: 'invalid' | 'mismatch' | 'unavailable' };
export async function verifyReviewBinding(value: unknown, result: EvaluationResult): Promise<Binding> {
  if (!validateReviewBundle(value)) return { status: 'invalid' };
  const run = frozen.provenance.run;
  if (!object(run) || value.snapshotVersion !== run.version || !sameData(result, frozen.result)) return { status: 'mismatch' };
  try {
    if (!globalThis.crypto?.subtle) return { status: 'unavailable' };
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(demoRaw));
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    return hash === value.snapshotSha256 ? { status: 'ready', bundle: value } : { status: 'mismatch' };
  } catch { return { status: 'unavailable' }; }
}

function CheckStatus({ check, gate = false }: { check: Check; gate?: boolean }) {
  const label = check.status === 'unverified' ? (gate ? '尚未人工复核' : '未验证') : check.status === 'verified' ? '已人工检查' : '无法确认';
  return <><span className="verification-check-status">{label}</span>{check.reviewer && <p>{check.reviewer.name} · {check.reviewer.role === 'independent' ? '独立复核' : '作者自审'} · {check.reviewedAt}</p>}{check.note && <p>{check.note}</p>}</>;
}

function RecordDetail({ record }: { record: ReviewRecord }) {
  const row = frozen.result.model_dfs[record.modelId][record.rowIndex];
  const items = statements(record.modelId, record.rowIndex);
  return <div className="verification-detail">
    <section className="verification-detail-block"><h3>原始回答</h3><p className="verification-original">{textValue(row.answer)}</p>
      <details className="verification-context"><summary>检索上下文原文</summary><pre className="verification-original">{typeof row.contexts === 'string' ? row.contexts : JSON.stringify(row.contexts, null, 2)}</pre></details>
    </section>
    <section className="verification-detail-block"><h3>人工核验</h3>{record.status === 'pending' ? <p className="verification-secondary verification-pending-note">尚未录入人工结论。</p> : <div className="verification-review-facts">
      <dl><div><dt>核验者</dt><dd>{record.reviewer!.name}</dd></div><div><dt>身份</dt><dd>{record.reviewer!.role === 'independent' ? '独立复核' : '作者自审'}</dd></div><div><dt>日期</dt><dd>{record.reviewedAt}</dd></div><div><dt>总体状态</dt><dd><span className={`verification-status verification-status--${record.status}`}>{statusLabels[record.status]}</span></dd></div><div><dt>抽取判断</dt><dd>{extractionLabels[record.answerReview!.extraction]}</dd></div><div><dt>拒答处理</dt><dd>{refusalLabels[record.answerReview!.refusal]}</dd></div></dl>
      <div className="verification-review-reason"><h4>回答级理由</h4><p>{displayReviewText(record.answerReview!.reason)}</p></div>
    </div>}
      <div className="verification-trace-block"><h4>Trace / 机器判定说明</h4>{items.length === 0 ? <p className="verification-empty-trace">原记录没有逐句判定，需核验抽取完整性与拒答处理。</p> : <div className="verification-statements">{items.map((item, index) => {
        const review = record.statementReviews.find(entry => entry.statementIndex === index);
        return <article key={index}><div className="verification-statement-title"><strong>陈述 {index + 1}</strong><span>机器原判定：{verdictLabels[item.verdict] ?? item.verdict}</span></div><p>{item.text}</p>{item.evidence && <blockquote><strong>机器引用</strong><br />{item.evidence}</blockquote>}{review && <div className="verification-human"><strong>人工判定：{verdictLabels[review.humanVerdict]}</strong><p>{displayReviewText(review.reason)}</p>{review.evidence && <blockquote><strong>人工依据</strong><br />{displayReviewText(review.evidence)}</blockquote>}</div>}</article>;
      })}</div>}</div>
    </section>
  </div>;
}

function HistoricalFailureCase() {
  // Read the original recorded gate; these metrics explain it without rerunning it.
  const run = failureCase.run;
  const model = run.models[0];
  return <section className="verification-failure-case" aria-labelledby="failure-case-title">
    <div className="verification-section-heading"><div><h2 id="failure-case-title">门禁拦截验证</h2><p>金融场景 · 存款保险 · {run.version}</p></div><span className="verification-failure-status">{run.gate_passed ? 'PASS' : 'FAIL'}</span></div>
    <p className="verification-secondary">以下是一次被质量门禁真实拦截的历史运行：指标未达门槛时，门禁拒绝放行。展示它是为了证明门禁的拦截能力，而非当前快照的评测结论。</p>
    <p className="verification-secondary">{modelName(model.model)} 正向上下文精度 {percent(model.pos_context_precision)}，低于 {percent(run.thresholds.context_precision)} 门槛。这是独立历史运行，不计入当前快照或人工核验统计。</p>
    <details className="verification-failure-details"><summary>查看问题、指标与门禁依据</summary>
      <section className="verification-failure-questions" aria-label="金融场景原始问题"><h3>原始问题与逐条指标</h3><p className="verification-secondary">以下为门禁主模型的 {failureCase.positiveRows.length} 条正向用例；门禁检查使用正向均值。</p>
        {failureCase.positiveRows.map(({ rowIndex, row }) => <article key={rowIndex}>
          <h4>{row.question}</h4><p className="verification-original" aria-label="原始回答">{row.answer}</p>
          <p className="verification-secondary">忠实度 {percent(Number(row.faithfulness))} · 上下文精度 {percent(Number(row.context_precision))}</p>
          <details className="verification-context"><summary>查看参考答案与检索上下文</summary><div><h4>参考答案</h4><p className="verification-original">{row.ground_truth}</p><h4>检索上下文原文</h4><p className="verification-original">{row.contexts}</p></div></details>
        </article>)}
      </section>
      <section className="verification-failure-gate" aria-label="金融场景原始门禁"><h3>原始门禁与指标对照</h3><dl><div><dt>原始门禁</dt><dd>{run.gate_passed ? 'PASS' : 'FAIL'}</dd></div><div><dt>判定模型</dt><dd>{modelName(model.model)}</dd></div><div><dt>正向忠实度</dt><dd>{percent(model.pos_faithfulness)}<small>门槛 ≥ {percent(run.thresholds.faithfulness)}</small></dd></div><div><dt>正向上下文精度</dt><dd>{percent(model.pos_context_precision)}<small>门槛 ≥ {percent(run.thresholds.context_precision)}</small></dd></div></dl>
        <p>第二个问题的上下文精度为 {percent(Number(failureCase.positiveRows[1].row.context_precision))}；归档正向均值 {model.pos_context_precision} 低于 {run.thresholds.context_precision} 门槛。正向忠实度达标，但两项均需达标，因此原始门禁为 FAIL。</p>
        <p className="verification-secondary">低上下文精度不等于已确认的回答事实错误。这是保存指标与阈值的对照分析；原档案未保存门禁说明或逐句 Trace，不补写原始说明、不推定幻觉条数，也不表示人工核验触发了拦截。</p>
      </section>
      <details className="verification-provenance"><summary>历史案例来源与原始数值</summary><p>运行时间：{run.timestamp} · 文档：{run.doc_name} · 测试集来源：{run.testset_source}</p><p>原始正向忠实度：{model.pos_faithfulness}；原始正向上下文精度：{model.pos_context_precision}；原始阈值：{run.thresholds.faithfulness} / {run.thresholds.context_precision}。</p><p>指标与问题从同一金融归档原样摘取；明细文件 SHA-256 与归档清单一致。历史 CLI 与 Web API 的评测入口不同，本案例不作为当前 API 的复跑证明。</p><dl>{Object.entries(failureCase.provenance.sourceHashes).map(([path, hash]) => <div key={path}><dt>{path}</dt><dd className="verification-hash">{hash}</dd></div>)}</dl></details>
    </details>
  </section>;
}

export function EvaluationVerificationView({ result, source, reviewBundle = reviewData }: { result: EvaluationResult | null; source: ResultSource | null; reviewBundle?: unknown }) {
  const [binding, setBinding] = useState<{ result: EvaluationResult; input: unknown; value: Binding } | null>(null);
  const [model, setModel] = useState('all');
  const [status, setStatus] = useState('all');
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    if (result && source === 'demo') void verifyReviewBinding(reviewBundle, result).then(value => {
      if (active) setBinding({ result, input: reviewBundle, value });
    });
    return () => { active = false; };
  }, [result, source, reviewBundle]);
  const message = !result ? '尚未加载可核验结果' : source !== 'demo' ? '本次结果尚无对应人工核验材料'
    : binding?.result !== result || binding.input !== reviewBundle ? '正在确认核验材料对应关系…'
      : binding.value.status === 'invalid' ? '核验材料格式无效'
        : binding.value.status === 'mismatch' ? '核验材料与当前结果不对应'
          : binding.value.status === 'unavailable' ? '无法确认材料对应关系' : null;
  if (message) return <section className="verification-state" aria-label="核验材料状态"><Icon name="file" size={28}/><h2 role="status">{message}</h2><p>{!result ? '请使用页头“加载演示”，再进入评测验证。' : source !== 'demo' ? '核验材料仅绑定已有演示快照，不适用于新的评测结果。' : message.startsWith('正在') ? '仅检查材料与快照是否对应，不执行评测。' : '暂不展示核验记录与统计。请检查核验包与冻结快照。'}</p></section>;
  if (binding?.value.status !== 'ready') return null;
  const bundle = binding.value.bundle;
  const totalAnswers = Object.values(frozen.result.model_dfs).reduce((sum, rows) => sum + rows.length, 0);
  const totalStatements = Object.entries(frozen.result.model_dfs).reduce((sum, [id, rows]) => sum + rows.reduce((count, _, index) => count + statements(id, index).length, 0), 0);
  const selectedStatements = bundle.records.reduce((sum, record) => sum + statements(record.modelId, record.rowIndex).length, 0);
  const pending = bundle.records.filter(record => record.status === 'pending').length;
  const filtered = bundle.records.filter(record => (model === 'all' || model === record.modelId) && (status === 'all' || status === record.status));
  const run = object(frozen.provenance.run) ? frozen.provenance.run : {};
  return <div className="verification-view">
    <section className="verification-scope" aria-label="人工核验范围">
      <dl className="verification-counts"><div><dt>已检查</dt><dd>{bundle.records.length - pending}<span>条</span></dd></div><div><dt>待核验</dt><dd>{pending}<span>条</span></dd></div><div><dt>未纳入</dt><dd>{totalAnswers - bundle.records.length}<span>条</span></dd></div></dl>
      <p>预选 {bundle.records.length}/{totalAnswers} 条回答，关联 {selectedStatements}/{totalStatements} 条逐句记录。已检查包含“无法确认”；定向核验范围不代表整体准确率。</p>
    </section>
    <section className="verification-records" aria-labelledby="verification-records-title">
      <div className="verification-section-heading"><div><h2 id="verification-records-title">核验记录</h2><p>按真实核验材料展示结论；机器原判定保持不变。</p></div><div className="verification-filters"><label>模型<select aria-label="模型" value={model} onChange={event => { setModel(event.target.value); setExpanded(null); }}><option value="all">全部模型</option>{Object.keys(targets).map(id => <option key={id} value={id}>{modelName(id)}</option>)}</select></label><label>核验状态<select aria-label="核验状态" value={status} onChange={event => { setStatus(event.target.value); setExpanded(null); }}><option value="all">全部状态</option>{Object.entries(statusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div></div>
      <div className="verification-table-scroll"><table className="verification-table"><caption className="verification-visually-hidden">预选回答的人工核验记录</caption><thead><tr><th scope="col">模型</th><th scope="col">问题</th><th scope="col">核验重点</th><th scope="col">状态</th><th scope="col">详情</th></tr></thead>{filtered.map(record => {
        const key = `${record.modelId}:${record.rowIndex}`;
        const detailId = `verification-detail-${Object.keys(targets).indexOf(record.modelId)}-${record.rowIndex}`;
        const row = frozen.result.model_dfs[record.modelId][record.rowIndex];
        return <tbody key={key} data-review-record={key}><tr><td>{modelName(record.modelId)}</td><td><span className="verification-question">{textValue(row.question)}</span><small>用例 {record.rowIndex + 1} · {row.case_type === 'positive' ? '正向' : '负向'}</small></td><td>{record.focus}</td><td><span className={`verification-status verification-status--${record.status}`}>{statusLabels[record.status]}</span></td><td><button className="verification-expand" aria-expanded={expanded === key} aria-controls={detailId} aria-label={`查看${modelName(record.modelId)}用例 ${record.rowIndex + 1} 的核验详情`} onClick={() => setExpanded(expanded === key ? null : key)}>{expanded === key ? '收起' : '展开'}<Icon name="chevron" size={14}/></button></td></tr><tr id={detailId} hidden={expanded !== key}><td colSpan={5}>{expanded === key && <RecordDetail record={record}/>}</td></tr></tbody>;
      })}</table></div>{filtered.length === 0 && <p className="verification-filter-empty" role="status">没有符合筛选条件的核验记录。</p>}
    </section>
    <HistoricalFailureCase/>
    <section className="verification-archive" aria-labelledby="verification-archive-title"><h2 id="verification-archive-title">依据档案</h2><div className="verification-checks"><section><h3>门禁规则核对</h3><CheckStatus check={bundle.gateRuleReview} gate/><p>依据原始首个有效模型的两项正向指标与保存阈值。阈值为现有配置，尚无业务校准证明。</p></section><section><h3>快照展示重现</h3><CheckStatus check={bundle.reproduction.snapshotReplay}/><p>记录是否按说明在干净环境打开同一快照。</p></section><section><h3>真实模型复跑</h3><CheckStatus check={bundle.reproduction.modelRerun}/><p>记录是否重新调用模型验证，不由快照加载推定。</p></section></div>
      <details className="verification-provenance"><summary>来源与适用边界</summary><dl><div><dt>快照版本</dt><dd>{bundle.snapshotVersion}</dd></div><div><dt>原运行时间</dt><dd>{textValue(run.timestamp)}</dd></div><div><dt>原文档</dt><dd>{textValue(run.doc_name)}</dd></div><div><dt>快照 SHA-256</dt><dd className="verification-hash">{bundle.snapshotSha256}</dd></div></dl><p>文件一致性只说明材料对应，不表示判定正确。原始门禁说明未记录；历史裁判模型版本、提示词版本和运行环境信息不完整，不补写推测。</p><p>引用出现在检索原文中，不等于原文足以支持陈述。空逐句记录不代表没有幻觉。其他运行的案例不并入本快照。</p></details>
    </section>
  </div>;
}
