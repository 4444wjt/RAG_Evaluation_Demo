import { useCallback, useEffect, useRef, useState } from 'react';
import { KU_STEPS, labHash, parseLabHash, type KUStep } from '../../labRoute';

/** Labels stay identical to the ones the workbench already used, so selectors and copy do not churn. */
export const KU_STEP_LABEL: Record<KUStep, string> = {
  sources: '选来源与版本',
  items: '输入条目',
  scan: '扫描差异',
  revise: '人工修订',
  review: '复核',
  export: '下载',
};

const readStep = (): KUStep => parseLabHash(window.location.hash)?.step ?? 'sources';

/**
 * Step state for the knowledge-update workbench. The step lives in the URL (`#/lab/knowledge-update/<step>`),
 * not in App state, so a reload or a pasted link lands on the same step and Back walks the steps.
 */
export function useKuStep(demo: boolean) {
  const [step, setStep] = useState<KUStep>(readStep);
  const [direction, setDirection] = useState<'fwd' | 'back'>('fwd');
  const previous = useRef<KUStep>(readStep());

  useEffect(() => {
    const sync = () => {
      const next = readStep();
      // Back/forward must keep the spatial direction honest, so compare positions instead of assuming forward.
      setDirection(KU_STEPS.indexOf(next) < KU_STEPS.indexOf(previous.current) ? 'back' : 'fwd');
      previous.current = next;
      setStep(next);
    };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => {
      window.removeEventListener('popstate', sync);
      window.removeEventListener('hashchange', sync);
    };
  }, []);

  const go = useCallback((next: KUStep) => {
    if (next === readStep()) return;
    setDirection(KU_STEPS.indexOf(next) < KU_STEPS.indexOf(previous.current) ? 'back' : 'fwd');
    previous.current = next;
    setStep(next);
    // Keep `labView` so App's popstate handler restores the same view instead of falling back to the hash target.
    const state = { ...window.history.state, labView: 'knowledge-update', kuStep: next };
    try {
      window.history.pushState(state, '', labHash('knowledge-update', demo, next));
    } catch {
      // Sandboxed frames can reject pushState; the hash fallback still renders the same step.
      window.location.hash = `#/lab/knowledge-update/${next}`;
    }
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, [demo]);

  return { step, direction, go };
}
