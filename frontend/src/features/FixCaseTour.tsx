import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';
import { Icon } from '../components/Icon';
import {
  CONTRAST, CORRECTED, HASH_NOTE, HELDOUT_FP, MAIN, MODEL, MODEL_NOTE, TRACE_NOTE, NO_TRACE, OTHER_CHANGED, PINNED_TREE, REPLAY, REPLAY_TEXT, REVIEW_CARD_FILENAME,
  RUNS, SAMPLE, SOURCE_FILES, VERDICT, evidence, fix, pinnedUrl, reviewCard, rowOf, runDate, score, sourceHashCommand,
} from './fixCase';
import type { CaseItem, Side, Statement } from './fixCase';
import './fix-case.css';

const formal = fix.formal;
const CATEGORY: Record<string, string> = Object.fromEntries(formal.categories.map((c) => [c.id, c.label]));
const threshold = formal.thresholds.faithfulness;
const STEPS = [
  { label: '看拦截', question: '门禁按什么规则拦截' },
  { label: '查原文', question: '资料已经给了什么，回答漏了什么' },
  { label: '看处理', question: '哪些行为改善了，哪些没有' },
  { label: '可复算', question: '结果能否回到原始记录重新算出' },
] as const;
const MARKERS = ['①', '②', '③', '④'];

/** The step lives in the history entry, so a reload or Back returns to the same step without adding entries. */
const savedStep = () => {
  const value: unknown = window.history.state?.fixStep;
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < STEPS.length ? value : 0;
};

/** Detailed sections further down the page already hold the full material; the tour only points at them. */
function jump(id: string) {
  const target = document.getElementById(id);
  target?.scrollIntoView?.({ block: 'start' });
  target?.focus({ preventScroll: true });
}

function download(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }));
  try {
    const link = document.createElement('a');
    try {
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
    } finally {
      link.remove();
    }
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function Chip({ tone, children }: { tone: 'success' | 'warning' | 'danger'; children: ReactNode }) {
  return <span className={`audit-chip audit-chip--${tone}`}>{children}</span>;
}

function Detail({ target, children }: { target: string; children: ReactNode }) {
  return <button type="button" className="fx-jump" onClick={() => jump(target)}>{children}<Icon name="arrow" size={13} className="fx-jump-arrow"/></button>;
}

const SCALE = [0.5, 1] as const;
const at = (value: number) => `${((value - SCALE[0]) / (SCALE[1] - SCALE[0])) * 100}%`;

function Verdict({ side }: { side: 'before' | 'after' }) {
  const gate = formal.gate[side];
  const { low, high } = gate.certainty;
  const label = side === 'before' ? '原运行' : '修复后';
  return <div className={`fx-tour-card fx-tour-card--${gate.passed ? 'passed' : 'blocked'}`}>
    <span className="audit-kicker">{`${label} · ${runDate(RUNS[side].run)} · 正向忠实度`}</span>
    <p className="fx-tour-figure"><strong>{gate.posFaithfulness}</strong><span>{`${gate.passed ? '≥' : '<'} 门槛 ${threshold}`}</span><Chip tone={gate.passed ? 'success' : 'danger'}>{`门禁 ${gate.passed ? 'PASS' : 'FAIL'}`}</Chip></p>
    <div className="audit-scale fx-tour-scale" role="img" aria-label={`${label}正向忠实度 ${gate.posFaithfulness}，95% 按题重抽区间 ${low}–${high}，门槛 ${threshold}`}>
      <div className="audit-scale-track">
        <span className="audit-band" style={{ '--at': at(low), '--to': at(high) } as CSSProperties}/>
        <span className="audit-threshold" style={{ '--at': at(threshold) } as CSSProperties}><small>{`门槛 ${threshold}`}</small></span>
        <span className={`audit-point fx-point--${gate.passed ? 'passed' : 'blocked'}`} style={{ '--at': at(gate.posFaithfulness) } as CSSProperties}/>
      </div>
      <div className="audit-scale-axis"><span>{SCALE[0]}</span><span>{`区间 ${low}–${high}`}</span><span>{SCALE[1]}</span></div>
    </div>
  </div>;
}

function NoTrace({ item }: { item: Side }) {
  return <span className="fx-tour-empty"><Icon name="alert" size={13}/>{`${NO_TRACE}${item.trace.status ? ` · trace_status：${item.trace.status}` : ''}`}</span>;
}

function Passage({ text, marks }: { text: string; marks: string[] }) {
  const hits = marks.map((mark) => ({ mark, start: text.indexOf(mark) })).filter((hit) => hit.start >= 0).sort((a, b) => a.start - b.start);
  const parts: ReactNode[] = [];
  let cursor = 0;
  hits.forEach(({ mark, start }) => { parts.push(text.slice(cursor, start), <mark key={start}>{mark}</mark>); cursor = start + mark.length; });
  parts.push(text.slice(cursor));
  return <p className="fx-passage-text">{parts}</p>;
}

function Passages({ item, only, grid }: { item: CaseItem; only?: number[]; grid?: boolean }) {
  const marked = (index: number) => item.highlights.filter((mark) => mark.context === index).map((mark) => mark.text);
  return <ol className={`fx-passages${grid ? ' fx-passages--grid' : ''}`}>{item.contexts.map((context, index) => (only && !only.includes(index)) ? null :
    <li key={index} value={index + 1} className={marked(index).length ? 'has-mark' : undefined}><span className="fx-passage-index" aria-hidden="true">{index + 1}</span><Passage text={context} marks={marked(index)}/></li>)}</ol>;
}

const refIds = (item: CaseItem) => [...new Set(item.highlights.flatMap((mark) => mark.referenceIds))].join('、');

function StepBlock() {
  const before = formal.gate.before;
  return <div className="fx-tour-grid">
    <Verdict side="before"/>
    <div className="fx-tour-stack">
      <p className="fx-tour-rule"><strong>门禁规则</strong>{`正向题的平均忠实度 ≥ ${threshold}，且平均上下文精度 ≥ ${formal.thresholds.contextPrecision}，两项都满足才放行。`}</p>
      <dl className="fx-tour-facts">
        <div><dt>批次</dt><dd><code>{RUNS.before.run}</code><small>{`${runDate(RUNS.before.run)} 正式运行`}</small></dd></div>
        <div><dt>回答与裁判请求配置</dt><dd>{MODEL}<small>{MODEL_NOTE}</small></dd></div>
        <div><dt>样本范围</dt><dd>{`${SAMPLE.total} 题 · 存款保险条例节选`}<small>{`正向 ${SAMPLE.positive} 题计入门禁；资料无答案 ${SAMPLE.negative} 题不计入`}</small></dd></div>
        <div><dt>另一项门槛</dt><dd>{`正向上下文精度 ${before.posContextPrecision} ≥ ${formal.thresholds.contextPrecision}`}<small>达标：这次拦截来自忠实度</small></dd></div>
      </dl>
      <Detail target="fx-gate-title">看两次门禁的完整对比</Detail>
    </div>
  </div>;
}

function StepSource() {
  const row = rowOf(MAIN.id);
  return <div className="fx-tour-stack">
    <div className="fx-tour-case-head"><span className="fx-tour-case-id">{MAIN.id}</span><span className="audit-note">{`${CATEGORY[rowOf(MAIN.id).category]}题 · 「${MAIN.title}」 · ${SAMPLE.total} 题中的一例`}</span></div>
    <p className="fx-tour-question">{MAIN.question}</p>
    <p className="audit-note"><span className="fx-tour-label">参考答案</span>{MAIN.groundTruth}</p>
    <section className="fx-tour-origin" aria-label={`${MAIN.id} 原始回答`}>
      <span className="audit-kicker">{`原始回答 · ${runDate(RUNS.before.run)} · 忠实度 ${score(row.before.faithfulness)}`}</span>
      <p className="fx-tour-answer fx-tour-answer--refusal"><b className="fx-tag fx-tag--refusal">拒答</b>{MAIN.before.answer}</p>
      <div className="fx-tour-origin-note"><NoTrace item={MAIN.before}/><span className="audit-note">不当成「不支持」，也不当成「通过」；这道正向题按 0 分计入门禁。</span></div>
    </section>
    <section className="fx-tour-stack" aria-label={`${MAIN.id} 同次检索到的原文`}>
      <span className="audit-kicker">{`同次检索到的原文 · ${MAIN.contexts.length} 段，按检索顺序 · 高亮为本题登记的原文依据（${refIds(MAIN)}）`}</span>
      <Passages item={MAIN} grid/>
    </section>
    <Detail target="fx-questions-title">{`在逐题对照中看全部 ${SAMPLE.total} 题`}</Detail>
  </div>;
}

function Statements({ items }: { items: Statement[] }) {
  return <ol className="fx-statements">{items.map((statement, index) => <li key={index}>
    <div><span className="fx-statement-index">{index + 1}</span><span>{statement.text}</span></div>
    <span className="fx-statement-verdict"><Chip tone={statement.verdict === 'supported' ? 'success' : 'danger'}>{`${statement.verdict} · ${VERDICT[statement.verdict] ?? statement.verdict}`}</Chip></span>
    <q>{statement.evidence}</q>
  </li>)}</ol>;
}

function StepChange() {
  const main = rowOf(MAIN.id);
  const contrast = rowOf(CONTRAST.id);
  const contrastContexts = CONTRAST.highlights.map((mark) => mark.context);
  return <div className="fx-tour-stack">
    <div className="fx-tour-stack fx-tour-stack--tight">
      <span className="audit-kicker">{`提示词改动 · 提交 ${fix.fix.commit} · backend/services/answering.py · 新增 1 行`}</span>
      <pre className="fx-diff"><code><b aria-label="新增">+</b>{fix.fix.line}</code></pre>
    </div>
    <div className="fx-tour-split fx-tour-split--stacked">
      <article className="fx-tour-card fx-tour-card--wide" aria-label={`${MAIN.id} 修复后`}>
        <header className="fx-tour-card-head"><span className="fx-tour-case-id">{MAIN.id}</span><span>{`修复后 · ${runDate(RUNS.after.run)}`}</span><Chip tone="success">拒答 → 纠正前提</Chip></header>
        <p className="fx-tour-answer">{MAIN.after.answer}</p>
        <span className="audit-kicker">{`原始逐句判定 ${MAIN.after.trace.statements.length} 条 · 忠实度 ${score(main.before.faithfulness)} → ${score(main.after.faithfulness)}`}</span>
        <p className="audit-note">{TRACE_NOTE}</p>
        <Statements items={MAIN.after.trace.statements}/>
      </article>
      <article className="fx-tour-card" aria-label={`${CONTRAST.id} 对照`}>
        <header className="fx-tour-card-head"><span className="fx-tour-case-id">{CONTRAST.id}</span><span>{`对照 · 「${CONTRAST.title}」`}</span><Chip tone="danger">仍拒答</Chip></header>
        <div className="fx-tour-pair">
          <div>
            <p className="fx-tour-question fx-tour-question--small">{CONTRAST.question}</p>
            <span className="audit-kicker">{`两次回答逐字相同 · 忠实度 ${score(contrast.before.faithfulness)} → ${score(contrast.after.faithfulness)}`}</span>
          </div>
          <div>
            <p className="fx-tour-answer fx-tour-answer--refusal"><b className="fx-tag fx-tag--refusal">拒答</b>{CONTRAST.after.answer}</p>
            <div className="fx-tour-empties"><span className="audit-note">修复前</span><NoTrace item={CONTRAST.before}/><span className="audit-note">修复后</span><NoTrace item={CONTRAST.after}/></div>
            <span className="audit-kicker">{`它的检索资料里有答案（登记依据 ${refIds(CONTRAST)}）`}</span>
            <Passages item={CONTRAST} only={contrastContexts}/>
          </div>
        </div>
      </article>
    </div>
    <ul className="fx-tour-caveats">
      <li><Icon name="alert" size={14}/><span>{`${MAIN.id} 只是一例：同批 ${CORRECTED.join('、')} 由拒答改为纠正前提，${CONTRAST.id} 仍拒答。`}<Detail target="fx-questions-title">逐题对照</Detail></span></li>
      <li><Icon name="alert" size={14}/><span>{`两次 ${SAMPLE.total} 题运行之间另有 ${OTHER_CHANGED.length} 个源文件不同，不能当作严格的单因素实验。只差这一句的严格对照是留出题：错误前提题 ${HELDOUT_FP.passBefore}/${HELDOUT_FP.questions} → ${HELDOUT_FP.passAfter}/${HELDOUT_FP.questions}，p = ${HELDOUT_FP.signTestP}。`}<Detail target="fx-heldout-title">留出题说明</Detail></span></li>
    </ul>
  </div>;
}

function StepRecompute() {
  const after = formal.gate.after;
  const rerun = fix.judgeRerun;
  return <div className="fx-tour-stack fx-recompute">
    <div className="fx-tour-grid fx-recompute-head">
      <Verdict side="after"/>
      <div className="fx-tour-stack">
        <p className="fx-callout"><Icon name="alert" size={15}/><span>{`95% 区间 ${after.certainty.low}–${after.certainty.high} 跨过 ${threshold}：PASS 也是「${after.certainty.state}」。门禁照规则放行，不等于确定达标。`}</span></p>
        <p className="fx-callout"><Icon name="alert" size={15}/><span>{`换 ${rerun.judgeModel} 作裁判重跑修复版：正向忠实度 ${rerun.gate.posFaithfulness}，门禁 ${rerun.gate.passed ? 'PASS' : 'FAIL'}；该次也重新生成回答，不能将翻转全部归于裁判。`}</span></p>
      </div>
    </div>
    <section className="fx-tour-stack" aria-label="原始来源与哈希">
      <span className="audit-kicker">原始来源 · 链接固定到提交 <a className="fx-tour-commit" href={PINNED_TREE} target="_blank" rel="noopener noreferrer">{evidence.pinned.commit.slice(0, 7)}</a></span>
      <ul className="fx-sources-list">{SOURCE_FILES.map((file) => <li key={file.path}>
        <a className="audit-source" href={pinnedUrl(file.path)} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>{`${file.side === 'before' ? '原运行' : '修复后'} · ${file.path.split('/').pop()}`}</a>
        <code title={file.path}>{`${file.path}`}</code>
        <code className="fx-sha" aria-label={`归档检出 SHA-256 ${file.sha256}`}>{`归档检出 sha256 ${file.sha256}`}</code>
        {file.gitBlobSha256 !== file.sha256 && <code className="fx-sha" aria-label={`链接原始文件 SHA-256 ${file.gitBlobSha256}`}>{`链接原始文件（LF）sha256 ${file.gitBlobSha256}`}</code>}
        <code className="fx-sha">{sourceHashCommand(file.path)}</code>
      </li>)}</ul>
      <p className="audit-note">基线 analysis.json 的归档检出为 CRLF，链接原始文件为 LF，只有换行不同；直接下载时请核对链接原始文件哈希。两份 API 响应的两种哈希相同。</p>
      <p className="audit-note">{HASH_NOTE}上面的 sha256sum 命令在仓库根目录执行，对实际检出文件计算哈希，再按对应字节版本核对。</p>
    </section>
    <section className="fx-tour-stack" aria-label="离线回放命令">
      <span className="audit-kicker">回放命令 · 仓库根目录离线运行，不调用模型、不需要密钥</span>
      <pre className="fx-tour-commands"><code>{REPLAY.map((line) => <span key={line.command}>{line.command}<span className="fx-comment">{`  # ${line.expect}`}</span>{'\n'}</span>)}</code></pre>
      <p className="audit-note">{`打开本页、下载复核卡都不执行新的评测；复核卡只整理上面这些已有记录。`}</p>
    </section>
  </div>;
}

const PANELS = [StepBlock, StepSource, StepChange, StepRecompute];

export function FixCaseTour() {
  const [step, setStepState] = useState(savedStep);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const cardContent = useMemo(() => reviewCard(), []);
  const [cardHash, setCardHash] = useState<{ status: 'pending' | 'unavailable' } | { status: 'ready'; sha256: string }>({ status: 'pending' });
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const tour = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const Panel = PANELS[step];

  useEffect(() => {
    // The workbench stays mounted on the home page; each history entry owns its step.
    const restore = () => { setStepState(savedStep()); setNotice(null); };
    window.addEventListener('hashchange', restore);
    window.addEventListener('popstate', restore);
    return () => {
      window.removeEventListener('hashchange', restore);
      window.removeEventListener('popstate', restore);
    };
  }, []);

  useEffect(() => {
    let active = true;
    const calculate = async () => {
      try {
        if (!window.crypto?.subtle) throw new Error('SHA-256 unavailable');
        const hash = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(cardContent));
        const sha256 = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('');
        if (active) setCardHash({ status: 'ready', sha256 });
      } catch {
        if (active) setCardHash({ status: 'unavailable' });
      }
    };
    void calculate();
    return () => { active = false; };
  }, [cardContent]);

  const select = (next: number) => {
    setStepState(next);
    window.history.replaceState({ ...window.history.state, fixStep: next }, '');
  };
  /** Tabs pattern: arrows, Home and End move between steps and open them. */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const last = STEPS.length - 1;
    const next = event.key === 'ArrowRight' ? (step === last ? 0 : step + 1) : event.key === 'ArrowLeft' ? (step === 0 ? last : step - 1)
      : event.key === 'Home' ? 0 : event.key === 'End' ? last : null;
    if (next === null) return;
    event.preventDefault();
    select(next);
    tabs.current[next]?.focus();
  };
  /** Previous / next keep the reader at the top of the new step, even after a long step on a phone. */
  const go = (next: number) => {
    select(next);
    requestAnimationFrame(() => {
      if ((tour.current?.getBoundingClientRect().top ?? 0) < 0) tour.current?.scrollIntoView?.({ block: 'start' });
      heading.current?.focus({ preventScroll: true });
    });
  };
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(REPLAY_TEXT);
      setNotice({ kind: 'success', text: `已复制 ${REPLAY.length} 条回放命令。` });
    } catch {
      setNotice({ kind: 'error', text: '复制失败：浏览器未允许写入剪贴板。命令在第 ④ 步，可选中后手动复制。' });
    }
  };
  const save = () => {
    try {
      download(cardContent, REVIEW_CARD_FILENAME);
      setNotice({ kind: 'success', text: `已发起浏览器下载 ${REVIEW_CARD_FILENAME}；未执行新的评测。是否保存由浏览器处理，本页无法检测取消或系统权限问题。` });
    } catch {
      setNotice({ kind: 'error', text: '下载准备失败，未能发起浏览器下载。请重试；第 ④ 步命令仍可手动复制。' });
    }
  };

  return <section ref={tour} className="audit-panel fx-tour" aria-labelledby="fx-tour-title">
    <div className="fx-tour-head">
      <div>
        <span className="fx-tour-kicker">四步导览 · 一个真实案例</span>
        <h2 id="fx-tour-title">一次发布拦截，怎样复核</h2>
        <p>{`主案例 ${MAIN.id}「${MAIN.title}」，对照 ${CONTRAST.id}「${CONTRAST.title}」。每一步都能回到下方的完整记录。`}</p>
      </div>
      <p className="fx-tour-provenance"><Icon name="file" size={14}/><span>本页来源：两次归档运行 <code>{RUNS.before.run}</code> 与 <code>{RUNS.after.run}</code> 的原始响应。与侧栏「当前评测」无关；进入本页不会载入演示快照，也不会替换工作区结果。</span></p>
    </div>
    <div className="fx-tour-steps" role="tablist" aria-label="复核步骤" style={{ '--progress': (step + 1) / STEPS.length } as CSSProperties} onKeyDown={onKeyDown}>
      {STEPS.map((item, index) => <button key={item.label} ref={(element) => { tabs.current[index] = element; }} type="button" role="tab" id={`fx-tour-tab-${index}`} className="fx-tour-step" aria-selected={index === step} aria-controls="fx-tour-panel" tabIndex={index === step ? 0 : -1} onClick={() => select(index)}>
        <span className="fx-tour-step-index" aria-hidden="true">{index + 1}</span><strong>{item.label}</strong><small>{item.question}</small>
      </button>)}
    </div>
    <div className="fx-tour-panel" role="tabpanel" id="fx-tour-panel" aria-labelledby={`fx-tour-tab-${step}`} key={step}>
      <h3 ref={heading} tabIndex={-1}>{`${MARKERS[step]} ${STEPS[step].label}`}<small>{STEPS[step].question}</small></h3>
      <Panel/>
    </div>
    <div className="fx-tour-bar">
      <div className="fx-tour-nav">
        <button type="button" className="btn btn-ghost" disabled={step === 0} onClick={() => go(step - 1)}><Icon name="arrow" size={15} className="fx-flip"/>上一步</button>
        {step < STEPS.length - 1 && <button type="button" className="btn btn-secondary" onClick={() => go(step + 1)}>{`下一步：${STEPS[step + 1].label}`}<Icon name="arrow" size={15}/></button>}
      </div>
      <div className="fx-tour-actions">
        <button type="button" className="btn btn-secondary" onClick={() => { void copy(); }}><Icon name="copy" size={15}/>复制回放命令</button>
        <button type="button" className="btn btn-primary" onClick={save}><Icon name="file" size={15}/>下载复核卡</button>
      </div>
      <p className="audit-note">下载文件为 UTF-8 Markdown，以下 SHA-256 对应同一份复核卡文件字节：<code className="fx-sha" aria-label="复核卡文件 SHA-256">{cardHash.status === 'ready' ? cardHash.sha256 : cardHash.status === 'pending' ? '计算中…' : '未取得 SHA-256（浏览器不支持或计算失败）'}</code>在保存目录执行 <code>{`sha256sum ${REVIEW_CARD_FILENAME}`}</code>，与此处核对。</p>
      <p className={`fx-tour-notice${notice?.kind === 'error' ? ' fx-tour-notice--error' : ''}`} role="status" aria-live="polite">{notice?.text}</p>
    </div>
  </section>;
}
