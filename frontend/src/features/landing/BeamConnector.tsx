// Adapted from Magic UI Animated Beam (MIT), copyright Magic UI.
// Source and retained license: frontend/THIRD_PARTY_NOTICES.md, licenses/magic-ui.txt.
import { useEffect, useId, useState } from 'react';
import type { CSSProperties, RefObject } from 'react';

export function BeamConnector({ containerRef, fromRef, toRef, delay, animated }: {
  containerRef: RefObject<HTMLElement | null>;
  fromRef: RefObject<HTMLElement | null>;
  toRef: RefObject<HTMLElement | null>;
  delay: number;
  animated: boolean;
}) {
  const id = useId();
  const [geometry, setGeometry] = useState({ path: '', width: 0, height: 0 });

  useEffect(() => {
    const container = containerRef.current;
    const from = fromRef.current;
    const to = toRef.current;
    if (!container || !from || !to) return;
    const updatePath = () => {
      const rect = container.getBoundingClientRect();
      const rectA = from.getBoundingClientRect();
      const rectB = to.getBoundingClientRect();
      const startX = rectA.left - rect.left + rectA.width / 2;
      const startY = rectA.top - rect.top + rectA.height;
      const endX = rectB.left - rect.left + rectB.width / 2;
      const endY = rectB.top - rect.top;
      // Keep measured endpoints while easing through the staggered vertical nodes.
      const middleY = (startY + endY) / 2;
      const path = `M ${startX},${startY} C ${startX},${middleY} ${endX},${middleY} ${endX},${endY}`;
      setGeometry({ path, width: rect.width, height: rect.height });
    };
    updatePath();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updatePath);
    for (const element of [container, from, to]) observer?.observe(element);
    if (!observer) window.addEventListener('resize', updatePath);
    return () => { observer?.disconnect(); window.removeEventListener('resize', updatePath); };
  }, [containerRef, fromRef, toRef]);

  return <svg className="evidence-beam" width={geometry.width} height={geometry.height} viewBox={`0 0 ${geometry.width} ${geometry.height}`} aria-hidden="true" fill="none">
    <path className="evidence-track" d={geometry.path} strokeWidth="1.2"/>
    <path className={`evidence-pulse${animated ? ' is-travelling' : ''}`} d={geometry.path} pathLength="1" stroke={`url(#${id})`} strokeWidth="2" strokeLinecap="round" style={{ '--beam-delay': `${delay}s` } as CSSProperties}/>
    <defs><linearGradient id={id} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={geometry.height}><stop stopColor="var(--brand)" stopOpacity=".35"/><stop offset=".5" stopColor="var(--brand)"/><stop offset="1" stopColor="var(--brand)" stopOpacity=".35"/></linearGradient></defs>
  </svg>;
}
