import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Icon } from '../components/Icon';
import { REPO_URL } from '../config';
import fixData from '../fixtures/fix-verification.json';
import { modelName, returnedModelText } from '../utils/format';
import './gate-audit.css';
import './fix-verification.css';

// Generated from the archived runs by tools/competition/build_fix_fixture.py; never edited by hand.
const data = fixData;
const formal = data.formal;
type Row = (typeof formal.rows)[number];
type Side = Row['before'] | Row['after'];
type Gate = typeof formal.gate.before;
type Tone = 'success' | 'warning' | 'danger' | 'neutral';

const CATEGORY: Record<string, string> = Object.fromEntries(formal.categories.map((c) => [c.id, c.label]));
const CHANGE: Record<string, { label: string; tone: Tone }> = {
  corrected: { label: '拒答 → 纠正前提', tone: 'success' },
  still_refused: { label: '仍拒答', tone: 'danger' },
  kept_refusal: { label: '仍正确拒答', tone: 'neutral' },
  answered_missing: { label: '改为作答', tone: 'danger' },
  newly_refused: { label: '改为拒答', tone: 'danger' },
  judge_only: { label: '回答相同，分数变了', tone: 'warning' },
  up: { label: '分数上升', tone: 'neutral' },
  down: { label: '分数下降', tone: 'warning' },
  same: { label: '不变', tone: 'neutral' },
};
/** Rows a reader should look at first: anything that moved, plus the one the fix did not reach. */
const NOTABLE = formal.rows.filter((row) => row.change !== 'same' && row.change !== 'kept_refusal');

const sourceLink = (path: string) => `${REPO_URL}/blob/master/${path}`;
const runDate = (path: string) => path.match(/(\d{4})(\d{2})(\d{2})T/)?.slice(2, 4).join('-') ?? '';
const BEFORE_DATE = runDate(data.source.baseline.path);
const AFTER_DATE = runDate(data.source.fixed.path);
const score = (value: number | null) => (value === null ? '—' : value.toFixed(2));
const signed = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toFixed(4)}`;
// The gate scale shows 0.5–1.0: every point and interval in both runs falls inside it.
const SCALE = [0.5, 1] as const;
const at = (value: number) => `${((value - SCALE[0]) / (SCALE[1] - SCALE[0])) * 100}%`;

function Chip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`audit-chip audit-chip--${tone}`}>{children}</span>;
}

function Source({ path, children }: { path: string; children: ReactNode }) {
  return <a className="audit-source" href={sourceLink(path)} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>{children}</a>;
}

function Subject() {
  return <section className="audit-subject" aria-label="对照对象">
    <div><span className="audit-kicker">对照对象</span><p>{`25 题正式运行 · 存款保险条例节选 · ${modelName(data.model)}`}</p></div>
    <div><span className="audit-kicker">两次运行</span><p>{`${BEFORE_DATE} 基线 → ${AFTER_DATE} 修复后，同题、同资料、同门槛`}</p></div>
    <p className="audit-note">本页数据由 tools/competition/build_fix_fixture.py 从三份归档生成：核对文件哈希，逐题重算正向均值、回放门禁，并由调用记录重算留出题结果。不改门禁，也不改判任何已记录的结论。</p>
  </section>;
}

function Story() {
  const refused = formal.rows.filter((row) => row.category === 'false_premise' && row.before.refusal).map((row) => row.id);
  const before = formal.gate.before;
  const after = formal.gate.after;
  const threshold = formal.thresholds.faithfulness;
  const otherChanges = data.code.changed.filter((path) => path !== 'backend/services/answering.py');
  return <section className="audit-panel" aria-labelledby="fx-story-title">
    <div className="audit-panel-head"><div><h2 id="fx-story-title">从拦下到放行</h2>
      <p>门禁先拦下了一次运行，原因找到后只改问答提示词，再按相同条件重跑。</p></div>
      <Chip tone="success">{'FAIL → PASS'}</Chip></div>
    <ol className="fx-story">
      <li className="fx-story-step fx-story-step--blocked"><strong>门禁拦下</strong><span>{`${BEFORE_DATE} · 正向忠实度 ${before.posFaithfulness} < ${threshold}，FAIL`}</span></li>
      <li className="fx-story-step"><strong>找到原因</strong><span>{`${refused.join('、')} 这 ${refused.length} 道错误前提题都回答「无法回答」，按 0 计`}</span></li>
      <li className="fx-story-step"><strong>改一句提示词</strong><span>{`提交 ${data.fix.commit}：「${data.fix.line}」`}</span></li>
      <li className="fx-story-step fx-story-step--passed"><strong>同条件重跑</strong><span>{`${AFTER_DATE} · 正向忠实度 ${after.posFaithfulness} ≥ ${threshold}，PASS`}</span></li>
    </ol>
    <ul className="fx-same" aria-label="两次运行之间">
      <li><Icon name="check" size={15}/>{`请求参数相同：${formal.request.compared.length} 项（资料、题集、模型、两项门槛）`}</li>
      <li><Icon name="check" size={15}/>{`25 题检索资料逐题相同（${formal.sameContexts} / 25）`}</li>
      <li><Icon name="check" size={15}/>门禁代码与 Ragas 裁判提示定义相同（哈希一致）</li>
      <li className="fx-same--diff"><Icon name="alert" size={15}/>{`另有 ${otherChanges.length} 个源文件不同（例如逐句核对 trace.py、流水线 pipeline.py），所以这次重跑不是严格的「只差一句」；严格对照见下方留出题`}</li>
    </ul>
  </section>;
}

function GateRun({ label, date, gate }: { label: string; date: string; gate: Gate }) {
  const threshold = formal.thresholds.faithfulness;
  const { low, high, state } = gate.certainty;
  const position = { '--at': at(gate.posFaithfulness) } as CSSProperties;
  return <li className="fx-run">
    <div className="audit-interval-head"><span>{label}<small>{date}</small></span><Chip tone={gate.passed ? 'success' : 'danger'}>{`门禁 ${gate.passed ? 'PASS' : 'FAIL'}`}</Chip></div>
    <div className="audit-scale" role="img" aria-label={`${label}：正向忠实度 ${gate.posFaithfulness}，95% 按题重抽区间 ${low}–${high}，门槛 ${threshold}`}>
      <div className="audit-scale-track">
        <span className="audit-band" style={{ '--at': at(low), '--to': at(high) } as CSSProperties}/>
        <span className="audit-threshold" style={{ '--at': at(threshold) } as CSSProperties}><small>{`门槛 ${threshold}`}</small></span>
        <span className={`audit-point fx-point fx-point--${gate.passed ? 'passed' : 'blocked'}`} style={position}/>
      </div>
      <div className="audit-scale-axis"><span>{SCALE[0]}</span><span>{SCALE[1]}</span></div>
    </div>
    <p className="fx-run-value"><strong>{gate.posFaithfulness}</strong><span>{`区间 ${low}–${high} · ${state}`}</span></p>
  </li>;
}

function GatePanel() {
  const { before, after } = formal.gate;
  return <section className="audit-panel" aria-labelledby="fx-gate-title">
    <div className="audit-panel-head"><div><h2 id="fx-gate-title">门禁两次的结论</h2>
      <p>点是正向忠实度，色带是按题有放回重抽 2 万次得到的 95% 区间，虚线是门槛。两次结论都由正式的门禁代码离线回放，与归档记录一致。</p></div></div>
    <ul className="fx-runs">
      <GateRun label="修复前" date={`${BEFORE_DATE} 基线`} gate={before}/>
      <GateRun label="修复后" date={`${AFTER_DATE} 重跑`} gate={after}/>
    </ul>
    <dl className="audit-facts fx-facts">
      <div><dt>正向上下文精度</dt><dd>{`${before.posContextPrecision} → ${after.posContextPrecision}`}</dd></div>
      <div><dt>资料无答案题的拒答准确率</dt><dd>{`${before.refusalAccuracy} → ${after.refusalAccuracy}`}</dd></div>
      <div><dt>答案正确性（25 题平均）</dt><dd>{`${before.answerCorrectness} → ${after.answerCorrectness}`}</dd></div>
      <div><dt>逐题回归对比</dt><dd>{formal.regression.status}<small>{`上下文精度：${formal.regression.contextPrecisionStatus}`}</small></dd></div>
    </dl>
    <p className="fx-callout"><Icon name="alert" size={15}/><span>{`两次的区间都跨过 ${formal.thresholds.faithfulness}：FAIL 和 PASS 都是「证据不足」。门禁照规则放行，不等于确定达标；20 道正向题样本小，再跑一次结论可能不同。`}</span></p>
  </section>;
}

function SideCell({ side, positive }: { side: Side; positive: boolean }) {
  return <span className="fx-side">
    <b className={`fx-tag${side.refusal ? ' fx-tag--refusal' : ''}`}>{side.refusal ? '拒答' : '作答'}</b>
    <span>{positive ? score(side.faithfulness) : '应拒答'}</span>
  </span>;
}

function QuestionRow({ row }: { row: Row }) {
  const change = CHANGE[row.change];
  const positive = row.caseType === 'positive';
  const trace = row.after.trace;
  return <li><details>
    <summary>
      <span className="fx-id">{row.id}</span>
      <span className="fx-cat">{CATEGORY[row.category]}</span>
      <span className="fx-question" title={row.question}>{row.question}</span>
      <SideCell side={row.before} positive={positive}/>
      <Icon name="arrow" size={13} className="fx-arrow"/>
      <SideCell side={row.after} positive={positive}/>
      <Chip tone={change.tone}>{change.label}</Chip>
    </summary>
    <div className="fx-detail">
      <p><span className="audit-kicker">问题</span>{row.question}</p>
      <p><span className="audit-kicker">参考答案</span>{row.groundTruth}</p>
      <div className="fx-answers">
        <div><span className="audit-kicker">{`修复前回答 · ${BEFORE_DATE}`}</span><p>{row.before.answer}</p></div>
        <div><span className="audit-kicker">{`修复后回答 · ${AFTER_DATE}`}</span><p>{row.after.answer}</p></div>
      </div>
      {row.sameAnswer && <p className="audit-note">{row.change === 'judge_only' ? `两次回答逐字相同，忠实度却从 ${score(row.before.faithfulness)} 变为 ${score(row.after.faithfulness)}：变化来自裁判打分的波动，不是回答变了。` : '两次回答逐字相同。'}</p>}
      {!row.after.refusal && trace.statements > 0 && <p className="audit-note">{`修复后逐句核对 ${trace.statements} 句：与资料矛盾 ${trace.factErrors} 句，资料中找不到依据 ${trace.fabrications} 句。`}</p>}
    </div>
  </details></li>;
}

function Questions() {
  const [notableOnly, setNotableOnly] = useState(true);
  const rows = notableOnly ? NOTABLE : formal.rows;
  const { contribution, regression } = formal;
  const drop = regression.drops[0];
  const dropRow = drop && formal.rows.find((row) => row.id === drop.id);
  const judgeOnly = formal.rows.filter((row) => row.change === 'judge_only');
  return <section className="audit-panel" aria-labelledby="fx-questions-title">
    <div className="audit-panel-head"><div><h2 id="fx-questions-title">逐题对照</h2>
      <p>同一道题，两次运行的回答并排。分数是该题的忠实度：回答拆成陈述后，有原文支持的占比；拒答按 0 计。点开一行看两次回答原文。</p></div>
      <div className="fx-filter" role="group" aria-label="显示范围">
        <button type="button" aria-pressed={notableOnly} onClick={() => setNotableOnly(true)}>{`有变化或未修好 ${NOTABLE.length} 题`}</button>
        <button type="button" aria-pressed={!notableOnly} onClick={() => setNotableOnly(false)}>{`全部 ${formal.rows.length} 题`}</button>
      </div></div>
    <div className="audit-table-scroll"><table className="fx-categories">
      <caption>按题型汇总</caption>
      <thead><tr><th scope="col">题型</th><th scope="col">题数</th><th scope="col">拒答（修复前 → 后）</th><th scope="col">平均忠实度（修复前 → 后）</th></tr></thead>
      <tbody>{formal.categories.map((c) => <tr key={c.id} className={c.id === 'false_premise' ? 'fx-row--focus' : undefined}>
        <th scope="row">{c.label}</th><td>{c.questions}</td>
        <td>{`${c.refusedBefore} → ${c.refusedAfter}`}{c.id === 'missing' && <small>资料没有答案，应当拒答</small>}</td>
        <td>{c.faithfulnessBefore === null ? '不计入门禁' : `${score(c.faithfulnessBefore)} → ${score(c.faithfulnessAfter)}`}</td>
      </tr>)}</tbody>
    </table></div>
    <div className="fx-list-head" aria-hidden="true"><span>题</span><span>题型</span><span>问题</span><span>修复前</span><span/><span>修复后</span><span>变化</span></div>
    <ul className="fx-list">{rows.map((row) => <QuestionRow key={row.id} row={row}/>)}</ul>
    <p className="audit-note">{`正向题平均变化 ${signed(regression.meanDelta)}：被纠正的 ${contribution.correctedIds.join('、')} 贡献 ${signed(contribution.corrected)}，其余 ${contribution.restCount} 题合计 ${signed(contribution.rest)}。逐题变化的 95% 区间 ${regression.interval[0]}–${regression.interval[1]}，包含 0。`}</p>
    <p className="audit-note">{`${judgeOnly.map((row) => `${row.id} 两次回答逐字相同，分数 ${score(row.before.faithfulness)} → ${score(row.after.faithfulness)}`).join('；')}${dropRow ? `；${dropRow.id} 两次回答只差「规定」两字，分数 ${score(dropRow.before.faithfulness)} → ${score(dropRow.after.faithfulness)}` : ''}。同一裁判对几乎相同的回答也会打出不同分数。`}</p>
  </section>;
}

function Heldout() {
  const held = data.heldout;
  const falsePremise = held.groups.find((g) => g.id === 'false_premise')!;
  const exception = held.notFullySupported[0];
  return <section className="audit-panel" aria-labelledby="fx-heldout-title">
    <div className="audit-panel-head"><div><h2 id="fx-heldout-title">换一批新题再验证</h2>
      <p>{`正式运行的 25 题正是发现问题的那批题，在它们上面通过不能排除过拟合。留出题是另写的 ${held.questions} 道新题，覆盖两份法规节选；同一模型、同一批资料，修复前后两组提示词只差这一句，严格对照。`}</p></div>
      <Chip tone="success">{`${falsePremise.passBefore} / ${falsePremise.questions} → ${falsePremise.passAfter} / ${falsePremise.questions}`}</Chip></div>
    <div className="audit-table-scroll"><table className="fx-heldout">
      <thead><tr><th scope="col">题型</th><th scope="col">题数</th><th scope="col">通过的标准</th><th scope="col">修复前通过</th><th scope="col">修复后通过</th><th scope="col">变好的题</th><th scope="col">变差</th></tr></thead>
      <tbody>{held.groups.map((g) => <tr key={g.id}>
        <th scope="row">{g.label}</th><td>{g.questions}</td>
        <td>{g.id === 'missing' ? '拒答' : '没有拒答，且没有与资料矛盾的陈述'}</td>
        <td>{g.passBefore}</td><td>{g.passAfter}</td>
        <td>{g.improved.length ? g.improved.join('、') : '—'}</td><td>{g.worsened.length}</td>
      </tr>)}</tbody>
    </table></div>
    <dl className="audit-facts fx-facts">
      <div><dt>错误前提题：全部陈述有依据</dt><dd>{`${falsePremise.fullySupportedBefore} → ${falsePremise.fullySupportedAfter}`}<small>{`/ ${falsePremise.questions}`}</small></dd></div>
      <div><dt>配对符号检验（双侧）</dt><dd>{`p = ${falsePremise.signTestP}`}<small>未达 0.05</small></dd></div>
      <div><dt>调用</dt><dd>{`${held.usage.requests} 次`}<small>{`${held.usage.errors} 次错误`}</small></dd></div>
      <div><dt>费用（按公开价估算）</dt><dd>{`约 ¥${held.usage.costCny.toFixed(2)}`}</dd></div>
    </dl>
    {exception && <p className="audit-note">{`修复后唯一没做到「全部陈述有依据」的是 ${exception.id}：「${exception.statements.join('」「')}」被判为资料中找不到依据。这句在否定题目的错误前提，资料里确实没有直接这样写；按逐句核对的规则照实计入。`}</p>}
    <p className="audit-note">{`实际返回模型 ${returnedModelText(held.returnedModels)}，温度 ${held.model.temperature}，一次运行。留出题不算 Ragas 指标，不给门禁结论。`}</p>
    <Source path={`${data.source.heldout.path}/README.md`}>留出题报告</Source>
  </section>;
}

function Limits() {
  const stillRefused = formal.rows.filter((row) => row.change === 'still_refused').map((row) => row.id);
  const rerun = data.judgeRerun;
  return <section className="audit-panel" aria-labelledby="fx-limits-title">
    <div className="audit-panel-head"><div><h2 id="fx-limits-title">这份对照不能说明什么</h2></div></div>
    <ul className="fx-limits">
      <li><strong>PASS 依赖裁判。</strong>{`本页两次都由 DeepSeek 自己打分。同一修复版本换 ${rerun.judgeModel} 作裁判重跑，正向忠实度 ${rerun.gate.posFaithfulness}，门禁 FAIL；${rerun.samePositive} 道回答逐字相同的正向题，平均分 ${rerun.samePositiveSelf} → ${rerun.samePositiveJudge}。`}<Source path={`${data.source.judge.path}/README.md`}>独立裁判复核</Source></li>
      <li><strong>25 题是发现问题的同一批题。</strong>留出题是新题，但只有 24 道，符号检验没有达到 0.05。</li>
      <li><strong>一个领域、一个模型、各跑一次。</strong>修复后的 PASS 区间仍跨过门槛，再跑一次可能不同。</li>
      <li><strong>只验证了这一句的效果。</strong>没有比较其他写法，也没有找出它为什么有效。</li>
      <li><strong>{`${stillRefused.join('、')} 没修好。`}</strong>它的检索资料里有「7个工作日内足额偿付存款」原文，模型仍然拒答；本次没有继续调整。</li>
      <li><strong>原因分析尚未人工确认。</strong>「提示词只给了拒答一个出口」是归档里 AI 辅助的分析；本页只列出可由数据核对的部分：这几道题都回答了「无法回答」。</li>
    </ul>
  </section>;
}

const COMMANDS = `python scripts/gate_replay.py ${data.source.fixed.path}/api-response.json --baseline ${data.source.baseline.path}/api-response.json
python tools/competition/build_fix_fixture.py --check`;

function Reproduce() {
  return <section className="audit-panel" aria-labelledby="fx-reproduce-title">
    <div className="audit-panel-head"><div><h2 id="fx-reproduce-title">自己复核</h2>
      <p>两条命令都离线运行，不调用模型、不需要密钥：第一条用正式门禁回放修复后的结果并逐题对比基线，第二条核对本页数据能否由三份归档重新算出。</p></div></div>
    <pre className="fx-commands"><code>{COMMANDS}</code></pre>
    <div className="fx-sources">
      <Source path={`${data.source.baseline.path}/README.md`}>{`${BEFORE_DATE} 正式运行`}</Source>
      <Source path={`${data.source.fixed.path}/README.md`}>{`${AFTER_DATE} 修复验证重跑`}</Source>
      <Source path={`${data.source.judge.path}/README.md`}>换裁判重跑</Source>
      <Source path="tools/competition/build_fix_fixture.py">生成本页数据的脚本</Source>
    </div>
  </section>;
}

export function FixVerificationView() {
  return <div className="audit-view fx-view">
    <Subject/>
    <Story/>
    <GatePanel/>
    <Questions/>
    <Heldout/>
    <Limits/>
    <Reproduce/>
  </div>;
}
