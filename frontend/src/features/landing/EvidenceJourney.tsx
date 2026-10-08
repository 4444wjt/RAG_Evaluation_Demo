import { createRef, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Icon } from '../../components/Icon';

const stages = [
  { label: '实验结论', icon: 'shield' },
  { label: '逐句判定', icon: 'trace' },
  { label: '检索上下文', icon: 'layers' },
] as const;

// A finite illustration of reading an existing result, not a live evaluation.
export function EvidenceJourney({ enabled }: { enabled: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const nodes = useMemo(() => stages.map(() => createRef<HTMLSpanElement>()), []);
  const gradient = useId();
  const finished = useRef(!enabled);
  const [started, setStarted] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [complete, setComplete] = useState(!enabled);
  const [geometry, setGeometry] = useState({ width: 0, height: 0, paths: [] as string[] });

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const measure = () => {
      const frame = element.getBoundingClientRect();
      const rectangles = nodes.map(node => node.current?.getBoundingClientRect());
      if (rectangles.some(rect => !rect)) return;
      const paths = rectangles.slice(1).map((to, index) => {
        const from = rectangles[index]!;
        const startX = from.right - frame.left + 8;
        const endX = to!.left - frame.left - 8;
        const startY = from.top - frame.top + from.height / 2;
        const endY = to!.top - frame.top + to!.height / 2;
        const middle = (startX + endX) / 2;
        return `M ${startX},${startY} C ${middle},${startY - 16} ${middle},${endY + 16} ${endX},${endY}`;
      });
      setGeometry({ width: frame.width, height: frame.height, paths });
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    nodes.forEach(node => { if (node.current) observer?.observe(node.current); });
    if (!observer) window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [nodes]);

  useEffect(() => {
    if (!enabled || finished.current) {
      finished.current = true;
      setComplete(true);
      setPlaying(false);
      return;
    }
    const element = host.current;
    if (!element) return;
    let visible = typeof IntersectionObserver === 'undefined';
    const sync = () => {
      const running = visible && !document.hidden && !finished.current;
      if (running) setStarted(true);
      setPlaying(running);
    };
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting && entry.intersectionRatio >= .15;
      sync();
    }, { threshold: [0, .15] });
    observer?.observe(element);
    document.addEventListener('visibilitychange', sync);
    sync();
    return () => { observer?.disconnect(); document.removeEventListener('visibilitychange', sync); };
  }, [enabled]);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const finish = (event: AnimationEvent) => {
      if (!(event.target instanceof Element) || !event.target.matches('.journey-pulse-1')) return;
      finished.current = true;
      setComplete(true);
      setPlaying(false);
    };
    element.addEventListener('animationend', finish);
    return () => element.removeEventListener('animationend', finish);
  }, []);

  return <div className="evidence-journey" ref={host} data-started={started} data-playing={playing} data-complete={complete}>
    <svg className="journey-connections" width={geometry.width} height={geometry.height} viewBox={`0 0 ${geometry.width} ${geometry.height}`} aria-hidden="true" fill="none">
      <defs><linearGradient id={gradient}><stop stopColor="var(--brand)" stopOpacity="0"/><stop offset=".55" stopColor="var(--brand)"/><stop offset="1" stopColor="var(--brand)" stopOpacity=".4"/></linearGradient></defs>
      {geometry.paths.map((path, index) => <g key={index}>
        <path className="journey-track" d={path}/>
        <path className={`journey-pulse journey-pulse-${index}`} d={path} pathLength="1" stroke={`url(#${gradient})`}/>
      </g>)}
    </svg>
    <ol className="journey-stages" aria-label="证据回查路径">
      {stages.map((stage, index) => <li key={stage.label}><span className={`journey-node journey-node-${index}`} ref={nodes[index]}><Icon name={stage.icon} size={18}/><span>{stage.label}</span></span></li>)}
    </ol>
    <p className="journey-caption">回查链路示意</p>
  </div>;
}
