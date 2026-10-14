import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import replayData from '../fixtures/formal-run-replay.json';
import { Icon } from '../components/Icon';
import { DEEPSEEK_VERSION } from '../config';
import fixData from '../fixtures/fix-verification.json';
import { labHash } from '../labRoute';
import { percent } from '../utils/format';

// Generated from the frozen 25-question run by tools/competition/build_replay_fixture.py; never edited by hand.
const replay = replayData;
const STEPS = ['① 取题', '② 逐句核对', '③ 逐题计分', '④ 门禁判定'];

/** Timeline offset (ms) for one animated element; the DOM always holds the final values, motion only reveals them. */
const at = (ms: number, extra?: Record<string, string | number>) => ({ '--at': `${ms}ms`, ...extra }) as CSSProperties;

/** Plays when at least a third of the card is visible and resets once it has fully left the viewport.
    Without IntersectionObserver, or with reduced motion requested, the final frame is shown statically. */
function useViewportReplay() {
  const ref = useRef<HTMLElement>(null);
  const [animated] = useState(() => typeof window !== 'undefined' && 'IntersectionObserver' in window
    && !(typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches));
  const [state, setState] = useState({ cycle: 0, running: false });
  useEffect(() => {
    const node = ref.current;
    if (!animated || !node) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.intersectionRatio >= 0.35) setState((current) => current.running ? current : { ...current, running: true });
      else if (!entry.isIntersecting) setState((current) => current.running ? { cycle: current.cycle + 1, running: false } : current);
    }, { threshold: [0, 0.35] });
    observer.observe(node);
    return () => observer.disconnect();
  }, [animated]);
  return { ref, animated, ...state };
}

export function FormalRunReplay() {
  const { ref, animated, cycle, running } = useViewportReplay();
  const { detail, rows, gate, thresholds } = replay;
  const answered = rows.filter((row) => !row.refusal);
  const answeredMean = answered.reduce((sum, row) => sum + row.faithfulness, 0) / answered.length;
  const statementTotal = rows.reduce((sum, row) => sum + row.total, 0);
  const runLabel = replay.source.runId.split('-')[0];
  return <section ref={ref} className={`formal-replay${animated ? ` replay-animated${running ? ' is-running' : ''}` : ''}`} aria-labelledby="formal-replay-title">
    <div className="formal-replay-inner" key={cycle}>
      <div className="formal-replay-head">
        <div><h2 id="formal-replay-title">正式运行回放</h2><p className="formal-replay-source">{`来源：25 题正式运行 · ${DEEPSEEK_VERSION} · ${runLabel}（冻结证据，非本页演示快照）`}</p></div>
        <ol className="formal-replay-steps" aria-label="回放步骤">{STEPS.map((step, index) => <li key={step} style={at([0, 600, 2600, 3800][index], { '--span': `${[600, 2000, 1200, 1200][index]}ms` })}>{step}</li>)}</ol>
      </div>
      <div className="formal-replay-body">
        <div className="replay-check">
          <p className="replay-question" data-at style={at(0)}><span>{`第 ${detail.rowIndex + 1} 题`}</span>{detail.question}</p>
          <p className="replay-answer" data-at style={at(150)} title={detail.answer}>{detail.answer}</p>
          <p className="replay-context" data-at style={at(300)} title={detail.context}><span>检索原文</span>{detail.context}</p>
          <ol className="replay-statements" aria-label="逐句核对结果">{detail.statements.map((item, index) => <li key={item.statement} className={item.verdict ? 'supported' : 'unsupported'} data-at style={at(600 + index * 100)}>
            <span>{item.statement}</span><strong data-at style={at(1000 + index * 400, { '--dur': 'var(--feedback-duration)' })}>{item.verdict ? '原文支持' : '裁判判为原文不支持'}</strong>
          </li>)}</ol>
        </div>
        <div className="replay-score">
          <p className="replay-score-note" data-at style={at(2600)}>{`每题得分 = 原文支持的陈述占比，共 ${statementTotal} 句`}</p>
          <ol className="replay-cells" aria-label={`${rows.length} 道正向题得分`}>{rows.map((row, index) => <li key={row.rowIndex} className={row.refusal ? 'refusal' : undefined} data-at style={at(2600 + index * 50)}
            aria-label={`第 ${row.rowIndex + 1} 题 ${row.supported}/${row.total}${row.refusal ? '，拒答，按 0 计' : ''}`} title={`第 ${row.rowIndex + 1} 题${row.refusal ? ' · 拒答，按 0 计' : ''}`}>
            <span>{`${row.supported}/${row.total}`}</span>{row.refusal && <small>拒答</small>}<i data-at style={at(2600 + index * 50, { '--score': row.faithfulness })}/>
          </li>)}</ol>
          <div className="replay-gate">
            <p className={`replay-gate-badge ${gate.passed ? 'passed' : 'blocked'}`} data-at style={at(4200)}>{`门禁 ${gate.passed ? 'PASS' : 'FAIL'}`}</p>
            <div data-at style={at(3800)}>
              <p>{`正向忠实度 ${percent(gate.posFaithfulness)} · 门槛 ≥ ${percent(thresholds.faithfulness)}`}</p>
              <p>{`正向上下文精度 ${percent(gate.posContextPrecision)} · 门槛 ≥ ${percent(thresholds.context_precision)}`}</p>
            </div>
            <p className="replay-gate-note" data-at style={at(4400)}>{`${rows.length - answered.length} 题为拒答（按 0 计）；其余 ${answered.length} 题均值 ${percent(answeredMean)}`}</p>
          </div>
        </div>
      </div>
      <p className="formal-replay-scope">此处逐句为 Ragas 忠实度内部的陈述判定，门禁的正向忠实度由它计分；界面其他位置的「逐句核对」是自研 Trace，与门禁并列、不参与计算。</p>
      <p className="formal-replay-next">{`这次 FAIL 之后：问答提示词加了一句，同条件重跑，正向忠实度 ${fixData.formal.gate.after.posFaithfulness}，门禁 PASS（结论确定性仍为「${fixData.formal.gate.after.certainty.state}」）。`}<a href={labHash('fix', true)}>查看修复验证<Icon name="arrow" size={13}/></a></p>
    </div>
  </section>;
}
