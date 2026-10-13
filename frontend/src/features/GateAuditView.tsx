import type { CSSProperties, ReactNode } from 'react';
import { Icon } from '../components/Icon';
import { REPO_URL } from '../config';
import { percent } from '../utils/format';
import auditData from '../fixtures/gate-audit.json';
import './gate-audit.css';

// Generated from frozen evidence by tools/competition/build_audit_fixture.py; never edited by hand.
const audit = auditData;
type Tone = 'success' | 'warning' | 'danger';

const sourceLink = (path: string) => `${REPO_URL}/blob/master/${path}`;
const decimal = (value: number) => value.toFixed(4);
const signed = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toFixed(4)}`;
const position = (value: number) => ({ '--at': `${Math.min(1, Math.max(0, value)) * 100}%` }) as CSSProperties;
const gateLabel = (passed: boolean) => (passed ? 'PASS' : 'FAIL');

function Chip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`audit-chip audit-chip--${tone}`}>{children}</span>;
}

function Question({ id, index, title, verdict, tone, source, children }: { id: string; index: string; title: string; verdict: string; tone: Tone; source: { label: string; path: string }; children: ReactNode }) {
  return <article className="audit-question" aria-labelledby={`${id}-title`}>
    <header><span className="audit-index" aria-hidden="true">{index}</span><h3 id={`${id}-title`}>{title}</h3><Chip tone={tone}>{verdict}</Chip></header>
    <div className="audit-question-body">{children}</div>
    <a className="audit-source" href={sourceLink(source.path)} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>{source.label}</a>
  </article>;
}

/** A 0–100% scale with the gate threshold marked; children are positioned with --at. Stacked scales label the threshold and axis once. */
function Scale({ label, threshold, children, marker = true, axis = true }: { label: string; threshold: number; children: ReactNode; marker?: boolean; axis?: boolean }) {
  return <div className="audit-scale" role="img" aria-label={label}>
    <div className="audit-scale-track">{children}<span className="audit-threshold" style={position(threshold)}>{marker && <small>{`门槛 ${percent(threshold)}`}</small>}</span></div>
    {axis && <div className="audit-scale-axis" aria-hidden="true"><span>0%</span><span>50%</span><span>100%</span></div>}
  </div>;
}

function Recompute() {
  const r = audit.recompute;
  return <Question id="audit-recompute" index="①" title="门禁结论能不能复算？" verdict="可精确重现" tone="success" source={{ label: '逐句判定重算 · 门禁回放', path: 'docs/gate-replay.md' }}>
    <ol className="audit-flow" aria-label="复算步骤">
      <li><strong>{r.statements}</strong><span>句逐句判定</span></li>
      <li><strong>{r.questions}</strong><span>道正向题得分</span></li>
      <li><strong>{decimal(r.recomputedFaithfulness)}</strong><span>重算正向忠实度</span></li>
    </ol>
    <dl className="audit-facts">
      <div><dt>归档正向忠实度</dt><dd>{decimal(r.recordedFaithfulness)}</dd></div>
      <div><dt>按逐句判定重算</dt><dd>{gateLabel(r.recomputedPassed)}</dd></div>
      <div><dt>门禁函数离线回放</dt><dd>{gateLabel(r.replayedPassed)}</dd></div>
      <div><dt>归档门禁</dt><dd>{gateLabel(r.recordedPassed)}</dd></div>
    </dl>
    <p className="audit-note">{`${r.supported} / ${r.statements} 句判为原文支持；${r.refusals} 道拒答题按 0 计。不调用模型，几秒内离线完成。`}</p>
  </Question>;
}

function Judges() {
  const { runs, noise } = audit.judges;
  const faith = noise.faithfulness;
  const threshold = audit.recompute.thresholds.faithfulness;
  return <Question id="audit-judges" index="②" title="换一个裁判，结论会变吗？" verdict={`${runs.length} 次均为 ${gateLabel(false)}`} tone="success" source={{ label: '独立裁判对照归档', path: 'docs/judge-comparison.md' }}>
    <ul className="audit-bars" aria-label="三次裁判的正向忠实度">{runs.map((run, index) => <li key={run.key}>
      <span>{run.label}</span>
      <Scale label={`${run.label} 正向忠实度 ${percent(run.posFaithfulness)}，门禁 ${gateLabel(run.passed)}`} threshold={threshold} marker={index === 0} axis={index === runs.length - 1}><i className="audit-bar" style={position(run.posFaithfulness)}/></Scale>
      <strong>{percent(run.posFaithfulness)}</strong>
    </li>)}</ul>
    <dl className="audit-facts audit-facts--two">
      <div><dt>逐题平均差 · 同裁判重判</dt><dd>{faith.sameJudge}</dd></div>
      <div><dt>逐题平均差 · 换裁判</dt><dd>{faith.crossJudge}<small>{`约 ${(faith.crossJudge / faith.sameJudge).toFixed(1)} 倍`}</small></dd></div>
    </dl>
    <p className="audit-note">{`跨过 0.8 的题：同裁判 ${faith.crossingSameJudge} 道，换裁判 ${faith.crossingCrossJudge} 道。同裁判重判的波动也是回归对比的噪声底线。`}</p>
  </Question>;
}

function Sensitivity() {
  const { cells, totals, statements } = audit.sensitivity;
  const variants = [...new Map(cells.map((cell) => [cell.variant, cell])).values()];
  const judges = [...new Map(cells.map((cell) => [cell.judge, cell.judgeLabel])).entries()];
  const cell = (judge: string, variant: string) => cells.find((item) => item.judge === judge && item.variant === variant)!;
  return <Question id="audit-sensitivity" index="③" title="证据变了，裁判会跟着变吗？" verdict={`${totals.matches} / ${totals.planned} 符合预期`} tone="success" source={{ label: '证据敏感性实验档案', path: 'docs/competition/judge-sensitivity/README.md' }}>
    <table className="audit-matrix">
      <caption>{`${statements} 条固定陈述，只改变提供给裁判的证据`}</caption>
      <thead><tr><th scope="col">证据条件</th><th scope="col">预期</th>{judges.map(([id, label]) => <th key={id} scope="col">{label}</th>)}</tr></thead>
      <tbody>{variants.map((variant) => <tr key={variant.variant}>
        <th scope="row">{variant.variantLabel}</th><td>{variant.expected}</td>
        {judges.map(([id]) => { const item = cell(id, variant.variant); return <td key={id} className={item.parseErrors ? 'audit-cell--warning' : 'audit-cell--ok'}>
          {`${item.matches} / ${item.valid}`}{item.parseErrors > 0 && <small>{`另 ${item.parseErrors} 条解析失败`}</small>}
        </td>; })}
      </tr>)}</tbody>
    </table>
    <p className="audit-note">{`${totals.valid} 个有效判定中 ${totals.matches} 个符合作者确认的预期；${totals.parseErrors} 条解析失败未计入，原始返回保留待人工解释。`}</p>
  </Question>;
}

function Certainty() {
  const { metrics, thresholds, resamples } = audit.certainty;
  const rows = [
    { key: 'faithfulness', label: '正向忠实度', metric: metrics.faithfulness, threshold: thresholds.faithfulness },
    { key: 'context_precision', label: '正向上下文精度', metric: metrics.context_precision, threshold: thresholds.context_precision },
  ];
  return <Question id="audit-certainty" index="④" title="这个 FAIL 有多确定？" verdict="保守拦截" tone="warning" source={{ label: '结论确定性分析', path: 'tools/competition/gate_uncertainty.py' }}>
    <ul className="audit-intervals">{rows.map(({ key, label, metric, threshold }) => {
      const [low, high] = metric.bootstrap95;
      return <li key={key}>
        <div className="audit-interval-head"><span>{label}</span><Chip tone={metric.state === '确定通过' ? 'success' : metric.state === '确定不通过' ? 'danger' : 'warning'}>{metric.state}</Chip></div>
        <Scale label={`${label} 点估计 ${percent(metric.mean)}，95% 区间 ${percent(low)}–${percent(high)}，门槛 ${percent(threshold)}`} threshold={threshold}>
          <i className="audit-band" style={{ ...position(low), '--to': `${high * 100}%` } as CSSProperties}/><i className="audit-point" style={position(metric.mean)}/>
        </Scale>
        <p>{`${percent(metric.mean)}，95% 区间 ${percent(low)}–${percent(high)}；重抽均值达到门槛 ${percent(metric.shareOfResamplesAtOrAboveThreshold)}`}</p>
      </li>;
    })}</ul>
    <p className="audit-note">{`按题有放回重抽 ${resamples} 次。区间跨过门槛：门禁照常拦截，但这是保守拦截，不是「确定不达标」；分析不用于改判。`}</p>
  </Question>;
}

function SelfCheck() {
  const { items, path } = audit.properties;
  const m = audit.mutation;
  return <section className="audit-panel" aria-labelledby="audit-selfcheck-title">
    <div className="audit-panel-head"><div><h2 id="audit-selfcheck-title">门禁代码本身对不对</h2><p>{`四问审计门禁的结论；这里检验门禁代码 ${m.target}。检验只新增测试，门禁代码未改。`}</p></div></div>
    <div className="audit-selfcheck">
      <div className="audit-properties">
        <h3>{`性质测试 · ${items.length} 条`}</h3>
        <p className="audit-note">Hypothesis 自动生成大量输入，检查对所有输入都应成立的规则；固定随机序列，结果可复现。</p>
        <ol aria-label="门禁性质">{items.map((item) => <li key={item.test} title={item.test}><Icon name="check" size={14}/><span>{item.description}</span></li>)}</ol>
        <a className="audit-source" href={sourceLink(path)} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>{path}</a>
      </div>
      <div className="audit-mutation">
        <h3>变异测试</h3>
        <p className="audit-note">逐个「改坏」门禁代码（如把 &lt; 改成 &lt;=、把 or 改成 and），看测试能否发现。</p>
        <div className="audit-score"><strong>{`${m.killed} / ${m.total}`}</strong><span>{`个变异被测试抓住（${percent(m.killed / m.total)}）`}</span></div>
        <ol className="audit-mutants" aria-label={`${m.total} 个变异：抓住 ${m.killed} 个，存活 ${m.survived} 个，超时 ${m.timeouts} 个`}>{m.mutants.map((mutant) =>
          <li key={mutant.id} className={`audit-mutant--${mutant.status}`} title={`变异 #${mutant.id} · 第 ${mutant.line} 行 · ${mutant.status === 'killed' ? '被抓住' : mutant.status === 'survived' ? '存活' : '超时'}`}/>)}</ol>
        <p className="audit-legend" aria-hidden="true"><span><i className="audit-mutant--killed"/>被抓住</span><span><i className="audit-mutant--survived"/>存活</span></p>
        <table className="audit-survivors">
          <caption>{`存活的 ${m.survived} 个变异都不影响裁决`}</caption>
          <thead><tr><th scope="col">变异</th><th scope="col">改动与说明</th></tr></thead>
          <tbody>{m.survivors.map((s) => <tr key={s.name}>
            <th scope="row">{`#${s.name.replace(/\D/g, '')}`}<small>{`第 ${s.line} 行`}</small></th>
            <td><code>{s.original}</code><code className="audit-mutated">{s.mutated}</code><p>{s.note}</p></td>
          </tr>)}</tbody>
        </table>
        <p className="audit-note">{`${m.tool} · ${m.date} ${m.environment} · 跳过 ${m.disabledMutationTypes.join(' / ')} 类变异。归档对应 gate.py SHA-256 ${m.targetSha256.slice(0, 12)}…，门禁代码改动后本页数据拒绝生成；CI 在门禁代码变动时于 Linux 重跑。`}</p>
        <a className="audit-source" href={sourceLink(m.source.path)} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>mutmut 原始报告</a>
      </div>
    </div>
  </section>;
}

function Regression() {
  const r = audit.regression;
  return <section className="audit-panel" aria-labelledby="audit-regression-title">
    <div className="audit-panel-head"><div><h2 id="audit-regression-title">回归对比样例：这次比上次差了吗</h2>
      <p>{`两次同题 5 题预跑：门禁 ${gateLabel(r.baselinePassed)} → ${gateLabel(r.candidatePassed)}。逐题配对后没有确定退步，CI 中只提示、不因此拦截。`}</p></div>
      <Chip tone={r.confirmed ? 'danger' : 'warning'}>{r.confirmed ? '确定退步' : '无确定退步'}</Chip></div>
    <div className="audit-table-scroll"><table className="audit-regression">
      <thead><tr><th scope="col">指标</th><th scope="col">基线</th><th scope="col">本次</th><th scope="col">配对题数</th><th scope="col">逐题平均变化</th><th scope="col">95% 区间</th><th scope="col">判定</th></tr></thead>
      <tbody>{r.metrics.map((metric) => <tr key={metric.key}>
        <th scope="row">{metric.label}</th><td>{decimal(metric.baseline)}</td><td>{decimal(metric.candidate)}</td><td>{metric.paired}</td>
        <td>{signed(metric.meanDelta)}</td><td>{`[${signed(metric.interval[0])}, ${signed(metric.interval[1])}]`}</td>
        <td><Chip tone={metric.status === '确定退步' ? 'danger' : metric.status === '可能退步' ? 'warning' : 'success'}>{metric.status}</Chip></td>
      </tr>)}</tbody>
    </table></div>
    <p className="audit-note">{`按题重抽 ${r.resamples} 次。「确定退步」= 区间整体低于 0，且下降超过同一裁判重判的波动，才会拦截。本例可配对的题只有 3–4 道，区间很宽，仅作演示。`}</p>
    <a className="audit-source" href={sourceLink('docs/gate-replay.md')} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>门禁回放与回归对比说明</a>
  </section>;
}

export function GateAuditView() {
  const r = audit.recompute;
  return <div className="audit-view">
    <section className="audit-subject" aria-label="审计对象">
      <div><span className="audit-kicker">审计对象</span><p>{`25 题正式运行 · DeepSeek · ${r.source.runId.split('-')[0]}`}</p></div>
      <div><span className="audit-kicker">原始门禁</span><p><Chip tone="danger">{gateLabel(r.recordedPassed)}</Chip>{`正向忠实度 ${percent(r.recordedFaithfulness)} < 门槛 ${percent(r.thresholds.faithfulness)}`}</p></div>
      <p className="audit-note">本页数据由 tools/competition/build_audit_fixture.py 从冻结证据生成并核对哈希：不调用模型，不改门禁规则，也不改判已记录的 FAIL。与「加载演示」的快照无关。</p>
    </section>
    <section className="audit-panel" aria-labelledby="audit-questions-title">
      <div className="audit-panel-head"><div><h2 id="audit-questions-title">门禁可信度四问</h2><p>门禁给出的 PASS / FAIL 决定能否发布，所以门禁结论本身也要被检验。</p></div></div>
      <div className="audit-questions"><Recompute/><Judges/><Sensitivity/><Certainty/></div>
    </section>
    <SelfCheck/>
    <Regression/>
    <p className="audit-boundary">这些结果来自同一领域的一次正式运行，不是通用基准，也不代表总体准确率。完整说明见 <a href={sourceLink('docs/competition/gate-credibility.md')} target="_blank" rel="noopener noreferrer">门禁可信度四问</a>。</p>
  </div>;
}
