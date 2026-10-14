import { createRef, useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../../components/Icon';
import { BeamConnector } from './BeamConnector';

const steps = [
  { name: 'Question', label: '提出问题', detail: '结合问题与参考答案，明确待验证事实', icon: 'file' },
  { name: 'Retrieval Context', label: '检索上下文', detail: '保留模型收到的原始上下文', icon: 'layers' },
  { name: 'Answer', label: '模型回答', detail: '保留原始回答，逐句展开陈述', icon: 'file' },
  { name: 'Evidence Check', label: '核对证据', detail: '对照事实判断，检查上下文支持', icon: 'trace' },
  { name: 'Quality Gate', label: '质量门禁', detail: '按样本范围与指标阈值检查结论', icon: 'shield' },
] as const;

const passDuration = 2400;
const totalDuration = passDuration * 2;
const boundaries = [450, 900, 1350, 1800, 2400, 2850, 3300, 3750, 4200, 4800];

export function EvidenceFlow({ enabled = true, startDelay = 0 }: { enabled?: boolean; startDelay?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const diagram = useRef<HTMLDivElement>(null);
  const nodes = useMemo(() => steps.map(() => createRef<HTMLSpanElement>()), []);
  const progress = useRef(0);
  const completed = useRef(!enabled);
  const [state, setState] = useState({ cycle: 0, active: enabled ? 0 : 4, finished: !enabled, running: false });
  // An optional lead-in lets the page's opening moment finish before the flow starts; it only applies once.
  const [waiting, setWaiting] = useState(enabled && startDelay > 0);

  useEffect(() => {
    if (!waiting) return;
    const timer = setTimeout(() => setWaiting(false), startDelay);
    return () => clearTimeout(timer);
  }, [waiting, startDelay]);

  useEffect(() => {
    if (!enabled || completed.current) {
      completed.current = true;
      progress.current = totalDuration;
      setState({ cycle: 1, active: 4, finished: true, running: false });
      return;
    }
    if (waiting) return;
    const element = host.current;
    if (!element) return;
    let visible = true;
    let startedAt: number | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const elapsed = () => Math.min(totalDuration, progress.current + (startedAt === null ? 0 : performance.now() - startedAt));
    const publish = (time: number, running: boolean) => setState({
      cycle: Math.min(1, Math.floor(time / passDuration)),
      active: time >= totalDuration ? 4 : Math.min(4, Math.floor((time % passDuration) / 450)),
      finished: time >= totalDuration,
      running,
    });
    const tick = () => {
      const time = elapsed();
      if (time >= totalDuration) {
        completed.current = true;
        progress.current = totalDuration;
        startedAt = null;
        publish(totalDuration, false);
        return;
      }
      publish(time, true);
      const next = boundaries.find(boundary => boundary > time) ?? totalDuration;
      timer = setTimeout(tick, Math.max(1, next - time));
    };
    const syncPlayback = () => {
      if (completed.current) return;
      if (visible && !document.hidden) {
        if (startedAt !== null) return;
        startedAt = performance.now();
        tick();
      } else {
        if (startedAt !== null) progress.current = elapsed();
        startedAt = null;
        clearTimeout(timer);
        publish(progress.current, false);
      }
    };
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      syncPlayback();
    });
    observer?.observe(element);
    document.addEventListener('visibilitychange', syncPlayback);
    syncPlayback();
    return () => {
      progress.current = elapsed();
      clearTimeout(timer);
      observer?.disconnect();
      document.removeEventListener('visibilitychange', syncPlayback);
    };
  }, [enabled, waiting]);

  return <div className="evidence-flow" ref={host} data-playing={state.running && !state.finished && enabled} data-paused={!state.running && !state.finished && enabled} data-cycle={state.cycle + 1} data-complete={state.finished}>
    <div className="evidence-flow-heading"><Icon name="trace" size={18}/><span>从问题到可检查的结论</span></div>
    <div className="evidence-diagram" ref={diagram}>
      {steps.slice(1).map((_, index) => <BeamConnector key={`${index}-${state.cycle}`} containerRef={diagram} fromRef={nodes[index]} toRef={nodes[index + 1]} delay={index * .45} animated={enabled && !waiting && !state.finished}/>)}
      <ol className="evidence-steps" aria-label="证据验证流程">
        {steps.map((step, index) => <li key={step.name} className={`evidence-step${state.active === index ? ' is-current' : ''}${index < state.active ? ' is-reached' : ''}`} aria-current={state.active === index ? 'step' : undefined}>
          <div className="evidence-step-content">
            <span className="evidence-node" ref={nodes[index]}><Icon name={step.icon} size={19}/></span>
            <span className="evidence-step-copy"><strong className="evidence-step-title">{step.label}</strong><span className="evidence-step-detail">{step.detail}</span></span>
            <span className="evidence-sequence" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
          </div>
        </li>)}
      </ol>
    </div>
    <p className="evidence-footnote">逐句证据看的是单条回答有没有依据，门禁看的是整批样本有没有达标——两回事，都不代表模型全面合格。</p>
  </div>;
}
