import { useEffect, useRef } from 'react';

const pointerAmplitude = 0.2;

// Fixed lattice values make the wind repeatable on entry, without a repeating
// animation cycle. Quintic interpolation keeps motion smooth between cells.
function windNoise(x: number, y: number) {
  const column = Math.floor(x);
  const row = Math.floor(y);
  const smooth = (value: number) => value * value * value * (value * (value * 6 - 15) + 10);
  const sample = (a: number, b: number) => {
    let hash = Math.imul(a, 374761393) ^ Math.imul(b, 668265263) ^ 0x6d2b79f5;
    hash = Math.imul(hash ^ (hash >>> 13), 1274126177);
    return ((hash ^ (hash >>> 16)) >>> 0) / 4294967295 * 2 - 1;
  };
  const horizontal = smooth(x - column);
  const vertical = smooth(y - row);
  const topLeft = sample(column, row);
  const bottomLeft = sample(column, row + 1);
  const top = topLeft + (sample(column + 1, row) - topLeft) * horizontal;
  const bottom = bottomLeft + (sample(column + 1, row + 1) - bottomLeft) * horizontal;
  return top + (bottom - top) * vertical;
}

/** A lightweight, original contour field drawn with the Canvas 2D API. */
export function ResearchField({ enabled }: { enabled: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phase = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let context: CanvasRenderingContext2D | null = null;
    try { context = canvas.getContext('2d', { alpha: true }); } catch { return; }
    if (!context) return;

    const host = canvas.closest<HTMLElement>('.landing-main');
    const hero = host?.querySelector<HTMLElement>('.landing-hero');
    if (!host || !hero) return;
    const ctx = context;
    const color = getComputedStyle(document.documentElement).getPropertyValue('--brand').trim() || '#F2C879';
    const pointer = { x: 0, y: 0, targetX: 0, targetY: 0, strength: 0, targetStrength: 0 };
    let width = 0;
    let height = 0;
    let heroHeight = 0;
    let frame = 0;
    let lastPaint = 0;
    let startedAt: number | null = null;
    let visible = true;
    let lineCount = 0;
    let pointCount = 0;
    let paints = 0;

    const setSize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.round(rect.width));
      height = Math.max(1, Math.round(rect.height));
      heroHeight = hero.getBoundingClientRect().height;
      const field = canvas.parentElement;
      field?.style.setProperty('--field-hero-height', `${heroHeight}px`);
      field?.style.setProperty('--field-fade-in', `${heroHeight * .12}px`);
      field?.style.setProperty('--field-fade-down', `${heroHeight * .8}px`);
      // Keep the backdrop intentionally soft and inexpensive: one canvas pixel per CSS pixel.
      canvas.width = width;
      canvas.height = height;
      lineCount = Math.ceil((width + 160) / 52) + 1;
      pointCount = Math.ceil((height + 72) / 36) + 1;
    };

    const draw = (time: number) => {
      ctx.clearRect(0, 0, width, height);
      ctx.strokeStyle = color;
      ctx.lineWidth = .75;
      const xStart = (width - 52 * (lineCount - 1)) / 2;
      const yStart = (heroHeight - 36 * Math.ceil((heroHeight + 72) / 36)) / 2;
      // Shared wind and broad local fields keep neighboring contours related,
      // while different time scales prevent a synchronized breathing rhythm.
      const driftX = windNoise(11.37, .43 + time / 9000) * 3;
      const driftY = windNoise(43.71, .67 + time / 13700) * 2;

      for (let line = 0; line < lineCount; line += 1) {
        ctx.globalAlpha = line % 5 === 0 ? .2 : .075;
        ctx.beginPath();
        for (let point = 0; point < pointCount; point += 1) {
          const sourceY = yStart + point * 36;
          const y = sourceY + driftY;
          const curveA = Math.sin(sourceY * .0041 + line * .43) * 18;
          const curveB = Math.sin(sourceY * .0018 + line * .19) * 15;
          const curveC = Math.sin(sourceY * .00075 + line * .71) * 8;
          const baseX = xStart + line * 52;
          // Willow-like lateral drift: two incommensurate periods with per-line
          // phase offsets — ~2.2s / 3.3s cycles, ~15px combined. The negative
          // spatial term carries the sway downward, like wind passing through.
          const sway =
            Math.sin(time / 2200 - sourceY * .0036 + line * 1.7) * 9 +
            Math.sin(time / 3300 - sourceY * .002 + line * .9) * 6;
          // The automatic displacement is bounded by 3 + 9 + 4 = 16px.
          // Noninteger offsets start within an evolving part of the wind field.
          const automaticBend = driftX
            + windNoise(baseX / 520 + sourceY / 640 + 8.73, .37 + time / 5200) * 9
            + windNoise(baseX / 350 - sourceY / 700 + 17.61, .73 + time / 11300) * 4;
          const distance = pointer.strength > .001 ? Math.hypot(baseX - pointer.x, y - pointer.y) : Infinity;
          // Pointer is auxiliary only: a smooth single-lobe easing — no high-
          // frequency wobble, no snap, no tracking.
          const influence = Math.max(0, 1 - distance / 150) ** 2;
          const pointerBend = pointerAmplitude * pointer.strength * influence * Math.sin((baseX - pointer.x) / 150 * (Math.PI / 2));
          const x = baseX + curveA + curveB + curveC + sway + automaticBend + pointerBend;
          if (point === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      paints += 1;
      canvas.dataset.frames = String(paints);
    };

    const activeTime = () => phase.current + (startedAt === null ? 0 : performance.now() - startedAt);
    const tick = (time: number) => {
      if (time - lastPaint >= 1000 / 30) {
        const elapsed = time - lastPaint;
        // Exponential smoothing is bounded and cannot overshoot like a spring.
        const blend = 1 - Math.exp(-elapsed / 240);
        pointer.x += (pointer.targetX - pointer.x) * blend;
        pointer.y += (pointer.targetY - pointer.y) * blend;
        pointer.strength += (pointer.targetStrength - pointer.strength) * blend;
        lastPaint = time;
        // Cap only drawing frequency; wind time follows real active time.
        draw(activeTime());
      }
      frame = requestAnimationFrame(tick);
    };
    const syncPlayback = () => {
      phase.current = activeTime();
      startedAt = null;
      cancelAnimationFrame(frame);
      const running = enabled && visible && !document.hidden;
      canvas.dataset.state = running ? 'running' : 'paused';
      if (running) {
        startedAt = performance.now();
        lastPaint = startedAt;
        frame = requestAnimationFrame(tick);
      } else {
        draw(phase.current);
      }
    };
    const onResize = () => { setSize(); draw(activeTime()); };
    const onPointerMove = (event: PointerEvent) => {
      if (!enabled || event.pointerType !== 'mouse') return;
      const rect = canvas.getBoundingClientRect();
      pointer.targetX = event.clientX - rect.left;
      pointer.targetY = event.clientY - rect.top;
      if (pointer.targetStrength === 0) {
        pointer.x = pointer.targetX;
        pointer.y = pointer.targetY;
      }
      pointer.targetStrength = 1;
    };
    const onPointerLeave = () => { pointer.targetStrength = 0; };
    const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onResize);
    const visibilityObserver = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      syncPlayback();
    });

    setSize();
    draw(phase.current);
    syncPlayback();
    resizeObserver?.observe(canvas);
    resizeObserver?.observe(hero);
    visibilityObserver?.observe(canvas);
    if (!resizeObserver) window.addEventListener('resize', onResize);
    host.addEventListener('pointermove', onPointerMove);
    host.addEventListener('pointerleave', onPointerLeave);
    document.addEventListener('visibilitychange', syncPlayback);

    return () => {
      phase.current = activeTime();
      startedAt = null;
      cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      visibilityObserver?.disconnect();
      window.removeEventListener('resize', onResize);
      host.removeEventListener('pointermove', onPointerMove);
      host.removeEventListener('pointerleave', onPointerLeave);
      document.removeEventListener('visibilitychange', syncPlayback);
    };
  }, [enabled]);

  return <div className="research-field" aria-hidden="true"><canvas ref={canvasRef} /></div>;
}
