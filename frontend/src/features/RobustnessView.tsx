import type { ReactNode } from 'react';
import { Icon } from '../components/Icon';
import { REPO_URL } from '../config';
import robustnessData from '../fixtures/robustness.json';
import { modelName, returnedModelText } from '../utils/format';
import './gate-audit.css';
import './robustness.css';

// Generated from the archived run by tools/competition/build_robustness_fixture.py; never edited by hand.
const data = robustnessData;
type Tone = 'success' | 'warning' | 'danger';
type Rate = { count: number; total: number; rate: number | null; wilson95: number[] | null };
type Condition = 'original' | 'shuffle' | 'noise' | 'remove' | 'counterfactual';
type Side = 'generator' | 'judge';
type Row = (typeof data.matrix)[number];
type Note = (typeof data.notes)[number];

const CONDITIONS: Condition[] = ['original', 'shuffle', 'noise', 'remove', 'counterfactual'];
const LABELS: Record<Condition, string> = { original: '原样', shuffle: '打乱', noise: '加噪', remove: '移除依据', counterfactual: '反事实' };
const SIDE_LABELS: Record<Side, string> = { generator: '生成侧', judge: '核对侧' };
const sourceLink = (path: string) => `${REPO_URL}/blob/master/${path}`;
const pct = (value: number) => `${Math.round(value * 100)}%`;
const fraction = (rate: Rate) => `${rate.count} / ${rate.total}`;
const range = (rate: Rate) => (rate.wilson95 ? `95% 区间 ${pct(rate.wilson95[0])}–${pct(rate.wilson95[1])}` : '无样本');
const ids = (kind: string, condition: Condition) => data.notes.filter((note) => note.kind === kind && note.condition === condition).map((note) => note.item);

function Chip({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`audit-chip audit-chip--${tone}`}>{children}</span>;
}

function Source({ path, children }: { path: string; children: ReactNode }) {
  return <a className="audit-source" href={sourceLink(path)} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>{children}</a>;
}

function Subject() {
  const s = data.source;
  const date = s.authorizedAt.slice(0, 10);
  return <section className="audit-subject" aria-label="体检对象">
    <div><span className="audit-kicker">体检对象</span><p>{`25 题正式运行的 ${data.items} 道正向题 · 存款保险条例`}</p></div>
    <div><span className="audit-kicker">本次运行</span><p>{`${date} · ${data.calls.paid} 次调用 · 约 ¥${data.usage.estimatedCostCny.toFixed(2)}（授权预算 ¥${s.budgetCny}）`}</p></div>
    <p className="audit-note">本页数据由 tools/competition/build_robustness_fixture.py 从归档生成：核对文件哈希，并用材料包与逐条调用记录重算全部汇总。不改门禁，也不改判已记录的 FAIL。</p>
  </section>;
}

const EXPECTATIONS: { condition: Condition; change: string; judge: string; generator: string }[] = [
  { condition: 'original', change: '不改，作同一次运行内的对照', judge: '—', generator: '—' },
  { condition: 'shuffle', change: '只改变资料顺序', judge: '结论不变', generator: '结论不变' },
  { condition: 'noise', change: `插入 ${data.parameters.noiseCount} 条其他题的资料（与本题依据无逐字重合）`, judge: '结论不变', generator: '不被噪声带偏' },
  { condition: 'remove', change: '去掉含依据的资料，用其他题的资料补足条数', judge: '标出无依据', generator: '拒答' },
  { condition: 'counterfactual', change: '只改依据原文里的数字（翻倍）和情态词（应当→无需、包括→不包括……）', judge: '标出矛盾', generator: '跟随资料或拒答，不沿用原值' },
];

function Conditions() {
  const skipped = data.skipped as Record<Condition, Record<string, number>>;
  return <section className="audit-panel" aria-labelledby="rb-conditions-title">
    <div className="audit-panel-head"><div><h2 id="rb-conditions-title">五种资料条件</h2>
      <p>把 RGB 的加噪、应拒答、反事实三种情形，和门禁可信度第三问的证据敏感性实验，合成一套能用于任何 RAG 应用的流程。同一套资料分别用于两侧：核对侧原回答不变、只换资料重新逐句核对；生成侧用改动后的资料重新作答再核对。</p></div></div>
    <div className="audit-table-scroll"><table className="rb-conditions">
      <thead><tr><th scope="col">条件</th><th scope="col">怎么改资料</th><th scope="col">核对侧应当</th><th scope="col">生成侧应当</th><th scope="col">可构造</th></tr></thead>
      <tbody>{EXPECTATIONS.map((e) => {
        const reasons = Object.entries(skipped[e.condition]);
        return <tr key={e.condition}>
          <th scope="row">{LABELS[e.condition]}</th><td>{e.change}</td><td>{e.judge}</td><td>{e.generator}</td>
          <td>{`${(data.built as Record<Condition, number>)[e.condition]} / ${data.items}`}{reasons.map(([reason, n]) => <small key={reason}>{`${n} 题${reason}`}</small>)}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <p className="audit-note">{`依据位置不用模型判断：取回答与参考答案中和资料逐字重合、至少 ${data.parameters.minShared} 个字、且在全部资料中出现不超过 ${pct(data.parameters.dfRatio)} 的片段，含这些片段的资料即依据资料。构造与被检的裁判无关，同一输入总得到同一套材料，付费运行前可逐条检查；构造不了的条件写明原因，不硬造。`}</p>
  </section>;
}

function Result({ title, expect, rate, tone, verdict, children }: { title: string; expect: string; rate: Rate; tone: Tone; verdict: string; children?: ReactNode }) {
  return <li className="rb-result">
    <div className="audit-interval-head"><span>{title}<small>{`应当：${expect}`}</small></span><Chip tone={tone}>{verdict}</Chip></div>
    <p className="rb-result-value"><strong>{fraction(rate)}</strong><span>{range(rate)}</span></p>
    {children}
  </li>;
}

function JudgeSide() {
  const j = data.judge;
  const cf = j.counterfactual;
  const hits = j.shuffle.unchanged.count + j.noise.unchanged.count + j.remove.flagged.count + cf.flagged.count;
  const total = j.shuffle.unchanged.total + j.noise.unchanged.total + j.remove.flagged.total + cf.flagged.total;
  const tone = (rate: Rate): Tone => (rate.count === rate.total ? 'success' : 'warning');
  const verdict = (rate: Rate) => (rate.count === rate.total ? '全部符合' : `${rate.total - rate.count} 例未达预期`);
  return <section className="audit-panel" aria-labelledby="rb-judge-title">
    <div className="audit-panel-head"><div><h2 id="rb-judge-title">核对侧：裁判跟着证据走吗</h2>
      <p>{`原回答不变，只换资料重新逐句核对。原样资料下 ${j.original.flagged.total} 道非拒答题的回答被标出 ${j.original.flagged.count} 题，以此为对照；拒答题没有可核对的陈述，核对侧跳过。`}</p></div>
      <Chip tone={hits === total ? 'success' : 'warning'}>{`${hits} / ${total} 符合预期`}</Chip></div>
    <ul className="rb-results">
      <Result title="打乱" expect="结论不变" rate={j.shuffle.unchanged} tone={tone(j.shuffle.unchanged)} verdict={verdict(j.shuffle.unchanged)}/>
      <Result title="加噪" expect="结论不变" rate={j.noise.unchanged} tone={tone(j.noise.unchanged)} verdict={verdict(j.noise.unchanged)}/>
      <Result title="移除依据" expect="标出无依据" rate={j.remove.flagged} tone={tone(j.remove.flagged)} verdict={verdict(j.remove.flagged)}/>
      <Result title="反事实" expect="标出矛盾" rate={cf.flagged} tone={tone(cf.flagged)} verdict={verdict(cf.flagged)}>
        <p className="audit-note">{`改数字 ${fraction(cf.byEdit.number)}，改情态词 ${fraction(cf.byEdit.modal)}；标出的都判为矛盾（${fraction(cf.contradicted)}）。`}</p>
      </Result>
    </ul>
    <p className="audit-note">这是第三问的自动化、可复用版本：第三问由作者手工构造 6 条陈述的材料，这里由程序按固定规则为每道题构造，换一个应用、换一批题也能直接跑。</p>
  </section>;
}

function GeneratorSide() {
  const g = data.generator;
  const cf = g.counterfactual;
  const adoption = cf.adoption;
  const answered = cf.refused.total - cf.refused.count;
  const turned = ids('改为作答', 'shuffle');
  const shuffleFlags = ids('核对新标出', 'shuffle');
  const noiseFlags = ids('核对新标出', 'noise');
  const stillAnswered = ids('移除依据后仍作答', 'remove');
  return <section className="audit-panel" aria-labelledby="rb-generator-title">
    <div className="audit-panel-head"><div><h2 id="rb-generator-title">生成侧：被测应用会不会被资料带偏</h2>
      <p>{`用平台的问答提示和 ${modelName(data.source.judgeModel)} 重新作答，再逐句核对。接入其他应用时，这一侧换成该应用的接口。`}</p></div>
      <Chip tone={adoption.kept.count === 0 ? 'success' : 'danger'}>{adoption.kept.count === 0 ? '没有拿原值顶替资料' : `${adoption.kept.count} 题沿用原值`}</Chip></div>
    <ul className="rb-results">
      <Result title="原样" expect="作为对照" rate={g.original.refused} tone="warning" verdict={`拒答 ${g.original.refused.count} 题`}>
        <p className="audit-note">{`拒答的都是错误前提题；作答的 ${g.original.answeredFlagged.total} 题核对标出 ${g.original.answeredFlagged.count} 题。`}</p>
      </Result>
      <Result title="打乱" expect="结论不变" rate={g.shuffle.refused} tone="warning" verdict={turned.length ? `${turned.length} 题改为作答` : '拒答不变'}>
        <p className="audit-note">{`${turned.length ? `${turned.join('、')} 原样下拒答，打乱后依据原文纠正了前提。` : ''}${shuffleFlags.length ? `${shuffleFlags.join('、')} 被核对标出。` : ''}`}</p>
      </Result>
      <Result title="加噪" expect="不被噪声带偏" rate={g.noise.refused} tone={g.noise.newlyRefused ? 'warning' : 'success'} verdict={g.noise.newlyRefused ? `新增拒答 ${g.noise.newlyRefused} 题` : '拒答不变'}>
        <p className="audit-note">{`作答的 ${g.noise.answeredFlagged.total} 题核对标出 ${g.noise.answeredFlagged.count} 题${noiseFlags.length ? `（${noiseFlags.join('、')}）` : ''}。`}</p>
      </Result>
      <Result title="移除依据" expect="拒答" rate={g.remove.refused} tone={g.remove.refused.count === g.remove.refused.total ? 'success' : 'warning'} verdict={`拒答 ${pct(g.remove.refused.rate ?? 0)}`}>
        <p className="audit-note">{stillAnswered.length ? `${stillAnswered.join('、')} 没有拒答，而是拿其他题的资料拼出了答案。` : '全部拒答。'}</p>
      </Result>
      <Result title="反事实" expect="跟随资料或拒答" rate={cf.refused} tone={adoption.kept.count ? 'danger' : 'success'} verdict={`沿用原值 ${adoption.kept.count} 题`}>
        <p className="audit-note">{`${cf.items} 题中拒答 ${cf.refused.count} 题（比原样新增 ${cf.newlyRefused} 题），作答 ${answered} 题全部依据改动后的资料，核对标出 ${cf.answeredFlagged.count} 题。改了数字的 ${adoption.followed.total} 题：跟随新数值 ${adoption.followed.count} 题，拒答 ${adoption.refused.count} 题。`}</p>
      </Result>
    </ul>
    <p className="audit-note">卡片中的大数字是拒答数。生成侧的「被核对标出」也包含核对本身的误判，需要结合下方逐条结果阅读。</p>
  </section>;
}

const CELL_TEXT: Record<Side, Record<string, string>> = {
  generator: { answered: '答', refused: '拒', flagged: '标', failed: '败' },
  judge: { clean: '支', flagged: '标', empty: '空', failed: '败' },
};

/** Whether a cell meets the condition's expectation; the original column is the baseline. */
function cellState(row: Row, side: Side, condition: Condition): 'base' | 'ok' | 'warn' | 'none' {
  const value = row[side][condition];
  if (!value) return 'none';
  const base = row[side].original;
  if (value === 'failed') return 'warn';
  if (condition === 'original') return value === 'flagged' ? 'warn' : 'base';
  if (condition === 'shuffle' || condition === 'noise') return value === base ? 'ok' : 'warn';
  if (side === 'judge') return value === 'flagged' ? 'ok' : 'warn';
  if (condition === 'remove') return value === 'refused' ? 'ok' : 'warn';
  return value === 'flagged' || row.adoption === 'kept' || row.adoption === 'both' ? 'warn' : 'ok';
}

function Matrix() {
  const describe = (row: Row, side: Side, condition: Condition) => {
    const value = row[side][condition];
    const skipped = (row.skipped as Partial<Record<Condition, string>>)[condition];
    return value ? `${row.id} · ${SIDE_LABELS[side]} · ${LABELS[condition]}：${CELL_TEXT[side][value]}` : `${row.id} · ${LABELS[condition]}：${skipped ?? '核对侧跳过拒答题'}`;
  };
  return <section className="audit-panel" aria-labelledby="rb-matrix-title">
    <div className="audit-panel-head"><div><h2 id="rb-matrix-title">逐题结果</h2>
      <p>绿色为符合预期，橙色为未达预期，灰色为对照列。悬停单元格可看说明，「—」表示该条件没有构造或不适用。</p></div></div>
    <div className="audit-table-scroll"><table className="rb-matrix">
      <thead>
        <tr><th scope="col" rowSpan={2}>题</th><th scope="colgroup" colSpan={5}>生成侧</th><th scope="colgroup" colSpan={5}>核对侧</th><th scope="col" rowSpan={2}>反事实改动</th></tr>
        <tr>{(['generator', 'judge'] as Side[]).flatMap((side) => CONDITIONS.map((c) => <th key={`${side}-${c}`} scope="col">{LABELS[c]}</th>))}</tr>
      </thead>
      <tbody>{data.matrix.map((row) => <tr key={row.id}>
        <th scope="row" title={row.question}>{row.id}</th>
        {(['generator', 'judge'] as Side[]).flatMap((side) => CONDITIONS.map((c) => {
          const value = row[side][c];
          return <td key={`${side}-${c}`} className={`rb-cell rb-cell--${cellState(row, side, c)}`} title={describe(row, side, c)}>{value ? CELL_TEXT[side][value] : '—'}</td>;
        }))}
        <td className="rb-edits">{row.edits.length ? row.edits.join('，') : '—'}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="audit-legend rb-legend"><span><b>答</b>作答，核对未标出</span><span><b>拒</b>拒答</span><span><b>标</b>被核对标出</span><span><b>支</b>核对判为全部支持</span></p>
  </section>;
}

function NoteItem({ note }: { note: Note }) {
  const edits = 'edits' in note ? (note.edits as { before: string; after: string }[] | undefined) : undefined;
  return <li><details>
    <summary><span className="rb-note-tag">{`${note.item} · ${SIDE_LABELS[note.side as Side]} · ${LABELS[note.condition as Condition]}`}</span><strong>{note.kind}</strong>{note.remark && <span className="rb-note-remark">{note.remark}</span>}</summary>
    <div className="rb-note-body">
      <p><span className="audit-kicker">问题</span>{note.question}</p>
      {edits && <p><span className="audit-kicker">资料改动</span>{edits.map((e) => `${e.before} → ${e.after}`).join('，')}</p>}
      <p><span className="audit-kicker">{note.side === 'generator' ? '重新生成的回答' : '原回答（未改动）'}</span>{note.answer}</p>
      {note.statements.length > 0 && <ul className="rb-statements">{note.statements.map((s, index) => <li key={index}>
        <span className={`rb-verdict rb-verdict--${s.verdict === '支持' ? 'ok' : 'warn'}`}>{s.verdict}</span><span>{s.text}{s.evidence && <small>{`引用：${s.evidence}`}</small>}</span>
      </li>)}</ul>}
    </div>
  </details></li>;
}

function Notes() {
  return <section className="audit-panel" aria-labelledby="rb-notes-title">
    <div className="audit-panel-head"><div><h2 id="rb-notes-title">值得注意的结果</h2>
      <p>按固定规则从调用记录中找出：打乱、加噪后结论改变，移除依据后仍作答，核对未达预期。灰字是作者解读。</p></div><Chip tone="warning">{`${data.notes.length} 条`}</Chip></div>
    <ul className="rb-notes">{data.notes.map((note) => <NoteItem key={note.id} note={note}/>)}</ul>
    <p className="audit-note">两点边界由此可见：逐句核对只查回答有没有依据，不查是否答对了题（移除依据后拼出的答案被判为有依据）；在否定叠加和修饰语改动上，核对也会误判或漏判。</p>
  </section>;
}

const COMMANDS = `python scripts/robustness_check.py prepare   --input 评测结果.json --dir work/rb
python scripts/robustness_check.py authorize --dir work/rb --by 授权人 --budget 5
python scripts/robustness_check.py run       --dir work/rb
python scripts/robustness_check.py analyze   --dir work/rb --output 归档目录`;
const CONTRACT = `POST <你的接口>   {"question": "…", "contexts": ["…", "…"]}
返回             {"answer": "…"}`;

function Usage() {
  return <section className="audit-panel" aria-labelledby="rb-usage-title">
    <div className="audit-panel-head"><div><h2 id="rb-usage-title">在你的 RAG 应用上体检</h2>
      <p>输入可以是平台的评测结果，也可以是每行一道题的 JSONL（question、contexts，以及 answer 或 ground_truth）。prepare 离线构造全部材料并估算费用；付费调用前须授权，授权绑定材料包与运行代码的哈希，不重试，按预算与请求数上限停止。</p></div></div>
    <div className="rb-usage">
      <div><h3>四步命令</h3><pre><code>{COMMANDS}</code></pre></div>
      <div><h3>接入被测应用（可选）</h3><pre><code>{CONTRACT}</code></pre>
        <p className="audit-note">加 --generator-endpoint 后，生成侧改为调用你的应用。应用须直接用传入的资料作答、不再自行检索，体检才能控制资料；访问令牌只从环境变量读取，不写入任何文件。</p></div>
    </div>
    <Source path="scripts/robustness_check.py">体检命令行与说明</Source>
  </section>;
}

export function RobustnessView() {
  return <div className="audit-view rb-view">
    <Subject/>
    <Conditions/>
    <JudgeSide/>
    <GeneratorSide/>
    <Matrix/>
    <Notes/>
    <Usage/>
    <p className="audit-boundary">{`一次运行、一个领域、同一模型既作答又核对；自动改动只覆盖数字和情态词，依据只认逐字重合，回答为概括改写时定位不到依据就不构造。实际模型 ${returnedModelText(data.source.returnedModels)}。完整说明与档案见 `}<a href={sourceLink('docs/competition/robustness/README.md')} target="_blank" rel="noopener noreferrer">鲁棒性体检</a>。</p>
  </div>;
}
