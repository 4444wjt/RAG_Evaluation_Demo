import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import replayData from '../fixtures/formal-run-replay.json';
import { Icon } from '../components/Icon';
import fixData from '../fixtures/fix-verification.json';
import { labHash } from '../labRoute';
import { percent } from '../utils/format';
import './formal-replay.css';

// Generated from the frozen 25-question run by tools/competition/build_replay_fixture.py; never edited by hand.
const replay = replayData;

/** Point → line → surface: each question's score, the run they form, then the mean against the threshold. */
const PHASES = [
  { label: '点', detail: '逐题得分', at: 0, span: 1300 },
  { label: '线', detail: '按题连接', at: 1300, span: 1200 },
  { label: '面', detail: '门槛差距', at: 2500, span: 1700 },
];

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

// Chart frame in viewBox units: scores 0–1 on the y axis, one column per positive question.
const W = 640, H = 220, LEFT = 40, RIGHT = 14, TOP = 16, BOTTOM = 26;
const x = (index: number, count: number) => LEFT + ((index + 0.5) * (W - LEFT - RIGHT)) / count;
const y = (value: number) => TOP + (1 - value) * (H - TOP - BOTTOM);

function ScoreChart() {
  const { rows, gate, thresholds } = replay;
  const points = rows.map((row, index) => [x(index, rows.length), y(row.faithfulness)] as const);
  const line = points.map(([px, py], index) => `${index ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
  const area = `${line} L${points[points.length - 1][0].toFixed(1)},${y(0)} L${points[0][0].toFixed(1)},${y(0)} Z`;
  const refused = rows.filter((row) => row.refusal).length;
  const mean = gate.posFaithfulness;
  const threshold = thresholds.faithfulness;
  return <svg className="fr-chart" viewBox={`0 0 ${W} ${H}`} role="img"
    aria-label={`${rows.length} 道正向题的得分：${refused} 题拒答按 0 计，均值 ${mean}，低于门槛 ${threshold}`}>
    <defs><linearGradient id="fr-area-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="fr-stop-top"/><stop offset="1" className="fr-stop-bottom"/></linearGradient></defs>
    {[0, 0.5, 1].map((tick) => <g key={tick} className="fr-grid"><line x1={LEFT} x2={W - RIGHT} y1={y(tick)} y2={y(tick)}/><text x={LEFT - 8} y={y(tick) + 4}>{tick.toFixed(1)}</text></g>)}
    <path className="fr-area" d={area} fill="url(#fr-area-fill)" data-at style={at(2500)}/>
    <path className="fr-line" d={line} pathLength={1} data-at style={at(1300)}/>
    <rect className="fr-gap" x={LEFT} width={W - LEFT - RIGHT} y={y(threshold)} height={y(mean) - y(threshold)} data-at style={at(3500)}/>
    <g className="fr-threshold"><line x1={LEFT} x2={W - RIGHT} y1={y(threshold)} y2={y(threshold)}/><text x={W - RIGHT} y={y(threshold) - 6}>{`门槛 ${threshold}`}</text></g>
    <g className="fr-mean" data-at style={at(3100)}><path d={`M${LEFT},${y(mean)} H${W - RIGHT}`} pathLength={1}/><text x={W - RIGHT} y={y(mean) + 15}>{`均值 ${mean}`}</text></g>
    {rows.map((row, index) => <circle key={row.rowIndex} className={`fr-dot${row.refusal ? ' fr-dot--refusal' : ''}`} cx={points[index][0]} cy={points[index][1]} r={row.refusal ? 4.5 : 4}
      data-at style={at(index * 60)}><title>{`第 ${row.rowIndex + 1} 题 ${row.supported}/${row.total}${row.refusal ? ' · 拒答，按 0 计' : ''}`}</title></circle>)}
  </svg>;
}

export function FormalRunReplay() {
  const { ref, animated, cycle, running } = useViewportReplay();
  const { rows, gate, thresholds } = replay;
  const refused = rows.filter((row) => row.refusal).length;
  const runLabel = replay.source.runId.split('-')[0];
  const fixed = fixData.formal.gate.after;
  return <section ref={ref} className={`formal-replay${animated ? ` replay-animated${running ? ' is-running' : ''}` : ''}`} aria-labelledby="formal-replay-title">
    <div className="formal-replay-inner" key={cycle}>
      <div className="formal-replay-head">
        <div><h2 id="formal-replay-title">正式运行回放</h2><p className="formal-replay-source">{`来源：25 题正式运行 · 请求配置 ${replay.source.model} · ${runLabel}（冻结证据，非本页演示快照）`}</p></div>
        <ol className="formal-replay-steps" aria-label="回放步骤">{PHASES.map((phase) => <li key={phase.label} style={at(phase.at, { '--span': `${phase.span}ms` })}><b>{phase.label}</b>{phase.detail}</li>)}</ol>
      </div>
      <div className="formal-replay-body">
        <figure className="fr-figure">
          <ScoreChart/>
          <figcaption className="fr-legend"><span className="fr-key fr-key--dot"/>{`${rows.length} 道正向题，按题序连接；均值按题等权计算`}<span className="fr-key fr-key--refusal"/>拒答按 0 计</figcaption>
        </figure>
        <div className="replay-gate">
          <p className={`replay-gate-badge ${gate.passed ? 'passed' : 'blocked'}`} data-at style={at(4000)}>{`门禁 ${gate.passed ? 'PASS' : 'FAIL'}`}</p>
          <p className="replay-gate-main" data-at style={at(4000)}>{`正向忠实度 ${percent(gate.posFaithfulness)} · 门槛 ≥ ${percent(thresholds.faithfulness)}`}</p>
          <p className="replay-gate-note" data-at style={at(4200)}>{`${refused} 题拒答拉低了均值；上下文精度 ${percent(gate.posContextPrecision)} 达标。`}</p>
          <p className="formal-replay-next" data-at style={at(4400)}>{`提示词调整后的重跑：${fixed.posFaithfulness}，PASS（另有源码变化，仍「${fixed.certainty.state}」）`}<a href={labHash('fix', true)}>查看修复验证<Icon name="arrow" size={13}/></a></p>
        </div>
      </div>
      <p className="formal-replay-scope">图中连线与阴影用于展示题目得分和门槛差距，不表示时间趋势或均值算法。得分来自 Ragas；自研 Trace 不参与门禁计算。</p>
    </div>
  </section>;
}
