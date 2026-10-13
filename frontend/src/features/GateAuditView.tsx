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

const STAGE_TONE: Record<string, string> = { 检索未找全: 'retrieval', 检索排序差: 'retrieval', 生成拒答: 'generation', 生成无依据: 'generation', 正常: 'rest', 无法判断: 'rest' };

function Attribution() {
  const a = audit.attribution;
  const score = audit.recompute.recordedFaithfulness;
  const threshold = audit.recompute.thresholds.faithfulness;
  const losses = Object.entries(a.faithfulnessLoss).filter(([, loss]) => loss > 0);
  const counts = a.counts as Record<string, number>;
  const lossLabel = (category: string) => (category === '正常' ? '其余题未满分' : category);
  const premise = a.flagged.length > 0 && a.flagged.every((q) => /^不(对|是|支持)/.test(q.groundTruth));
  return <section className="audit-panel" aria-labelledby="audit-attribution-title">
    <div className="audit-panel-head"><div><h2 id="audit-attribution-title">失败归因：该修检索还是生成</h2>
      <p>门禁只回答能不能放行。这里按逐题指标，把正向忠实度离满分的差距拆到检索与生成两段；只读已有指标，不调用模型，不影响裁决。</p></div>
      {a.primaryStage && <Chip tone="warning">{`主要在${a.primaryStage}`}</Chip>}</div>
    <div className="audit-split" role="img" aria-label={`正向忠实度 ${percent(score)}；${losses.map(([c, loss]) => `${lossLabel(c)} 拉低 ${percent(loss)}`).join('，')}；门槛 ${percent(threshold)}`}>
      <div className="audit-split-track">
        <i className="audit-split--score" style={{ flexBasis: `${score * 100}%` }}/>
        {losses.map(([category, loss]) => <i key={category} className={`audit-split--${STAGE_TONE[category]}`} style={{ flexBasis: `${loss * 100}%` }}/>)}
        <span className="audit-threshold" style={position(threshold)}><small>{`门槛 ${percent(threshold)}`}</small></span>
      </div>
      <ul className="audit-split-legend" aria-hidden="true">
        <li><i className="audit-split--score"/>{`正向忠实度 ${percent(score)}`}</li>
        {losses.map(([category, loss]) => <li key={category}><i className={`audit-split--${STAGE_TONE[category]}`}/>{`${lossLabel(category)} −${percent(loss)}`}</li>)}
      </ul>
    </div>
    <dl className="audit-facts">
      <div><dt>检索造成</dt><dd>{decimal(a.retrievalLoss)}</dd></div>
      <div><dt>生成造成</dt><dd>{decimal(a.generationLoss)}</dd></div>
      <div><dt>生成拒答</dt><dd>{`${counts['生成拒答']} 道`}</dd></div>
      <div><dt>正向题</dt><dd>{`${a.questions} 道`}</dd></div>
    </dl>
    <div className="audit-table-scroll"><table className="audit-flagged">
      <caption>{`${a.flagged.length} 道需要处理的题（其余 ${counts['正常']} 道归为正常）`}</caption>
      <thead><tr><th scope="col">题</th><th scope="col">归因</th><th scope="col">召回 · 忠实度</th><th scope="col">问题与回答</th><th scope="col">参考答案</th></tr></thead>
      <tbody>{a.flagged.map((q) => <tr key={q.index}>
        <th scope="row">{`#${q.index + 1}`}</th><td>{q.category}</td>
        <td>{`${q.context_recall === null ? '—' : percent(q.context_recall)} · ${q.faithfulness === null ? '—' : decimal(q.faithfulness)}`}</td>
        <td><p>{q.question}</p><p className="audit-answer">{q.answer}</p></td><td>{q.groundTruth}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="audit-note">{premise
      ? `${a.flagged.length} 道题的参考答案都是「不对 / 不支持」：问题带错误前提，所需原文已检索到，但问答提示词只给了「无法回答」一个出口，模型没有依据原文纠正前提。它们合计拉低正向忠实度 ${percent(a.generationLoss)}，是这次 FAIL 的主要来源。`
      : `检索造成的差距 ${decimal(a.retrievalLoss)}，生成造成的差距 ${decimal(a.generationLoss)}。`}</p>
    {a.advice.map((item) => <p key={item.category} className="audit-note"><strong>{`${item.category}：`}</strong>{item.advice}</p>)}
    <p className="audit-note">{`判定依据：召回 < ${a.floors.recall} 为检索未找全，精度 < ${a.floors.precision} 为排序差，检索正常而忠实度 < ${a.floors.faithfulness} 时再区分拒答与无依据。阈值是诊断经验值，不是门禁阈值。`}</p>
    <a className="audit-source" href={sourceLink('docs/gate-replay.md')} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>失败归因说明（门禁回放文档）</a>
  </section>;
}

const GOLD_LABELS = [['none', '无'], ['contradictory', '矛盾'], ['unverifiable', '无法验证']] as const;
const PREDICTED_LABELS = [['none', '无幻觉'], ['contradictory', '矛盾'], ['unverifiable', '无依据'], ['no_statement', '未产出陈述']] as const;
const interval = (range: number[] | null) => (range ? `${percent(range[0])}–${percent(range[1])}` : '—');

function External() {
  const e = audit.external;
  const { anah, rgb, counterfactual: cf, adjusted } = e;
  const b = anah.binary;
  const confusion = anah.confusion as Record<string, Record<string, number>>;
  const returned = Object.keys(e.returnedModels).join('、');
  const conditions = [
    { key: 'noise', title: `噪声 · ${rgb.noise.items} 题`, detail: '3 篇相关 + 2 篇无关资料', tone: 'success' as Tone, verdict: '无误伤',
      result: `答对 ${rgb.noise.accuracy.count} / ${rgb.noise.items}；正确回答被判有幻觉 ${rgb.noise.flaggedWhenCorrect.count} / ${rgb.noise.flaggedWhenCorrect.total}` },
    { key: 'reject', title: `应拒答 · ${rgb.reject.items} 题`, detail: '5 篇都与问题无关', tone: 'success' as Tone, verdict: `拒答 ${percent(rgb.reject.refusal.rate ?? 0)}`,
      result: `拒答 ${rgb.reject.refusal.count} / ${rgb.reject.items}；作答的 ${rgb.reject.answeredFlaggedByTrace.total} 题中标出 ${rgb.reject.answeredFlaggedByTrace.count} 题自行推出的结论` },
    { key: 'counterfactual', title: `反事实 · ${rgb.counterfactual.items} 题`, detail: '相关资料本身含错误事实', tone: 'warning' as Tone, verdict: '查不出资料错误',
      result: `只给出错误答案 ${cf.onlyFake} 题，逐句核对标出 ${cf.onlyFakeFlagged} 题；其余照抄资料的判为有依据` },
  ];
  return <section className="audit-panel" aria-labelledby="audit-external-title">
    <div className="audit-panel-head"><div><h2 id="audit-external-title">外部基准：逐句核对本身准不准</h2>
      <p>{`逐句核对是平台的核心功能（门禁分数则来自 Ragas 忠实度的另一次逐条判定）。用两个公开、带人工标注的中文数据集检验逐句核对：ANAH 逐句幻觉标注 ${anah.sentences} 句，RGB 新闻问答 ${rgb.noise.items + rgb.reject.items + rgb.counterfactual.items} 题。直接调用生产代码，${e.requests} 次请求一次跑完，不重试；按返回用量估算 ¥${e.usage.estimatedCostCny.toFixed(2)}（授权预算 ¥${e.budgetCny}）。`}</p></div>
      <Chip tone="success">{`幻觉召回 ${percent(b.recall ?? 0)}`}</Chip></div>
    <div className="audit-external">
      <div className="audit-external-col">
        <h3>ANAH · 对照人工逐句标注</h3>
        <dl className="audit-facts audit-facts--three">
          <div><dt>召回率</dt><dd>{decimal(b.recall ?? 0)}<small>{interval(b.recallWilson95)}</small></dd></div>
          <div><dt>精确率</dt><dd>{decimal(b.precision ?? 0)}<small>{interval(b.precisionWilson95)}</small></dd></div>
          <div><dt>特异度</dt><dd>{decimal(b.specificity ?? 0)}<small>{interval(b.specificityWilson95)}</small></dd></div>
        </dl>
        <table className="audit-confusion">
          <caption>{`人工标注（行）× 逐句核对（列），每类等量抽取 ${anah.sentences / 3} 句`}</caption>
          <thead><tr><th scope="col">人工 ＼ 核对</th>{PREDICTED_LABELS.map(([key, label]) => <th key={key} scope="col">{label}</th>)}</tr></thead>
          <tbody>{GOLD_LABELS.map(([gold, label]) => <tr key={gold}><th scope="row">{label}</th>
            {PREDICTED_LABELS.map(([key]) => { const value = confusion[gold][key] ?? 0;
              const hit = key === gold; const flagged = gold !== 'none' && (key === 'contradictory' || key === 'unverifiable');
              return <td key={key} className={hit ? 'audit-confusion--hit' : flagged ? 'audit-confusion--near' : value ? 'audit-confusion--miss' : undefined}>{value}</td>; })}
          </tr>)}</tbody>
        </table>
        <p className="audit-note">{`有幻觉 = 矛盾或无依据，即只区分支持与否。等量抽样会抬高精确率：按可抽样句子中 ${percent(adjusted.prevalence)} 有幻觉折算，约为 ${decimal(adjusted.precision)}。人工判无幻觉的句子有 ${percent(1 - (b.specificity ?? 0))} 被判无依据，多为概括、评价性表述：核对比人工更严。`}</p>
      </div>
      <div className="audit-external-col">
        <h3>RGB · 新闻领域三种证据条件</h3>
        <ul className="audit-conditions">{conditions.map((c) => <li key={c.key}>
          <div className="audit-interval-head"><span>{c.title}<small>{c.detail}</small></span><Chip tone={c.tone}>{c.verdict}</Chip></div>
          <p>{c.result}</p>
        </li>)}</ul>
        <p className="audit-note">逐句核对检查回答与证据是否一致，不检查证据本身对不对：资料写错时，照抄的回答会被判为有依据。被标出的几题，是同批资料里另有矛盾片段，或模型自行加了资料外的说法。这类问题要靠资料来源的可信度检查，不能指望逐句核对。</p>
      </div>
    </div>
    <p className="audit-note">{`数据集：${e.sources.anah.name}（${e.sources.anah.license}）与 ${e.sources.rgb.name}（${e.sources.rgb.license}），固定版本并核对源文件哈希；仓库只存编号与判定，不转载原文。请求模型 ${e.model}，服务端返回 ${returned}。单次运行、单一裁判，是抽样检验，不代表总体准确率。`}</p>
    <a className="audit-source" href={sourceLink('docs/competition/external-validation/README.md')} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>外部基准验证档案</a>
  </section>;
}

const coverage = (range: number[]) => (range[0] === range[1] ? percent(range[0]) : `${percent(range[0])}–${percent(range[1])}`);

function Calibration() {
  const c = audit.calibration;
  const sim = c.simulation;
  const answered = c.flips.answeredOnly;
  const estimators = sim.estimators as Record<string, string>;
  const cov = sim.coverage as Record<'invariant' | 'shifted', Record<string, number[]>>;
  const [best, worst] = [c.tipping[0], c.tipping[c.tipping.length - 1]];
  return <section className="audit-panel audit-calibration" aria-labelledby="audit-calibration-title">
    <div className="audit-panel-head"><div><h2 id="audit-calibration-title">裁判判错校准：把裁判误差也算进区间</h2>
      <p>第四问的区间只计题目抽样，把每条逐句判定都当作正确。裁判会判错，所以区间要同时计入「题目抽样」和「裁判判错」。目前已实现裁判误差分析工具和模拟验证，真实人工校准待完成；完成之前，不报告校正后的门禁结论。</p></div>
      <Chip tone="warning">人工校准待完成</Chip></div>
    <div className="audit-external">
      <section className="audit-external-col" aria-labelledby="audit-calibration-sensitivity">
        <header className="audit-calibration-head">
          <div className="audit-interval-head"><h3 id="audit-calibration-sensitivity">敏感性分析</h3><Chip tone="success">已完成</Chip></div>
          <p className="audit-calibration-scope">不需要人工标注。只回答结论取决于几条判定，不估计裁判实际判错了多少。</p>
        </header>
        <dl className="audit-facts audit-facts--two">
          <div><dt>门禁裁判口径</dt><dd>{percent(c.judgeValue)}<small>{`${c.judgedSupported} / ${c.statements} 条判为支持`}</small></dd></div>
          <div><dt>翻成 PASS 最少改判</dt><dd>{`${answered.flips} 条`}<small>{`计入拒答题 ${c.flips.overall.flips} 条`}</small></dd></div>
        </dl>
        <ul className="audit-calibration-list" aria-label="需要改判的陈述">{answered.statements.map((s) => <li key={`${s.caseId}-${s.statement}`}><span>{`${s.caseId} 第 ${s.statement} 条`}</span>{s.text}</li>)}</ul>
        <div className="audit-calibration-notes">
          <p className="audit-note">作答题中判为不支持的只有这 <b className="audit-hl audit-hl--warning">{`${answered.flips} 条`}</b>，要全部改判为支持，均值才恰好到 <b className="audit-hl audit-hl--brand">{percent(answered.after ?? 0)}</b>。</p>
          <p className="audit-note">{`换成裁判误差的说法：特异度为 ${best.specificity.toFixed(2)} 时，只要灵敏度低于 `}<b className="audit-hl audit-hl--info">{best.sensitivity.toFixed(3)}</b>{`，校正后的点估计就达到门槛（特异度 ${worst.specificity.toFixed(2)} 时为 `}<b className="audit-hl audit-hl--info">{worst.sensitivity.toFixed(3)}</b>）。逐句核对在 ANAH 上测得的灵敏度是 <b className="audit-hl">{sim.judgeRates.q1.toFixed(3)}</b>，但门禁用的是 Ragas 的另一套提示词，不能直接套用。</p>
        </div>
      </section>
      <section className="audit-external-col" aria-labelledby="audit-calibration-simulation">
        <header className="audit-calibration-head">
          <div className="audit-interval-head"><h3 id="audit-calibration-simulation">模拟验证</h3><Chip tone="success">已完成</Chip></div>
          <p className="audit-calibration-scope">检验区间方法在给定的裁判误差下是否可靠。误差借用 ANAH 上的实测值，不代表门禁裁判的真实误差。</p>
        </header>
        <table className="audit-confusion">
          <caption>{`95% 区间的实际覆盖率（${sim.cells / 2} 种设定的范围，每种模拟 ${sim.replicates} 次）`}</caption>
          <thead><tr><th scope="col">区间</th><th scope="col">裁判判定不变</th><th scope="col">裁判判定漂移</th></tr></thead>
          <tbody>{Object.keys(estimators).map((name) => <tr key={name} className={name === 'leeClustered' ? 'audit-row--ours' : name === 'naive' ? 'audit-row--naive' : undefined}><th scope="row">{estimators[name]}</th><td>{coverage(cov.invariant[name])}</td><td>{coverage(cov.shifted[name])}</td></tr>)}</tbody>
        </table>
        <div className="audit-calibration-notes">
          <p className="audit-note">{`按正式运行的题目结构（${c.questions} 题、${c.statements} 句）模拟。`}<b className="audit-hl audit-hl--danger">只计题目抽样会严重失准</b>：真实值 90%（达标）时，有 <b className="audit-hl audit-hl--danger">{`${percent(sim.naiveFailWhenPassing[0])}–${percent(sim.naiveFailWhenPassing[1])}`}</b> 的模拟判为「确定不通过」。</p>
          <p className="audit-note">{`陈述按题聚集，当作独立样本会偏窄，所以按题折算有效样本量。裁判判定漂移时，靠校准的校正会失准，同一次运行的人工标注（PPI 或全部标注）不受影响。`}</p>
        </div>
      </section>
    </div>
    <section className="audit-calibration-status" aria-labelledby="audit-calibration-human">
      <div className="audit-calibration-human">
        <div className="audit-interval-head"><h3 id="audit-calibration-human">真实人工校准</h3><Chip tone="warning">待标注</Chip></div>
        <p><b className="audit-hl audit-hl--warning">待完成：</b>{`请 1–2 位未参与本项目的人，独立标注门禁所用的 ${c.annotation.statements} 条陈述（${c.annotation.questions} 题，约 30 分钟）。标注页不显示裁判的判定；不用模型代替人工标注。`}</p>
        <p className="audit-note">分析口径在标注前写定：分歧与「无法判断」不剔除，按全部算支持、全部算不支持给出两个边界，分母与门禁相同；只标了部分题时，只用从第一题起连续标完、至少 10 题的部分，达不到就不给区间。</p>
      </div>
      <a className="btn btn-secondary" href="#/lab/annotate"><Icon name="pen" size={15}/>打开标注页</a>
    </section>
    <a className="audit-source" href={sourceLink('docs/competition/judge-calibration/README.md')} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>裁判判错校准说明</a>
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
    <Attribution/>
    <External/>
    <Calibration/>
    <SelfCheck/>
    <Regression/>
    <p className="audit-boundary">四问与失败归因来自同一领域的一次正式运行；外部基准是两个公开数据集上的抽样检验。两者都不代表总体准确率。完整说明见 <a href={sourceLink('docs/competition/gate-credibility.md')} target="_blank" rel="noopener noreferrer">门禁可信度四问</a>。</p>
  </div>;
}
