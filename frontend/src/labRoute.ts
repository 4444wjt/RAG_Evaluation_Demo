import type { View } from './api/types';

export type LabView = View | 'models' | 'verification' | 'audit' | 'fix' | 'robustness' | 'blindspot' | 'annotate' | 'parent-context' | 'citation-version' | 'knowledge-update';
export type LabTarget = 'lab' | 'demo' | 'workspace';

/** The knowledge-update workbench splits into six addressable steps so each one can be linked and reloaded. */
export const KU_STEPS = ['sources', 'items', 'scan', 'revise', 'review', 'export'] as const;
export type KUStep = (typeof KU_STEPS)[number];

/** #/lab, #/lab/demo, #/lab/workspace keep their meaning; an optional trailing view segment deep-links a workbench view. */
const LAB_HASH = /^#\/lab(?:\/(demo))?(?:\/(overview|workspace|models|comparison|trace|report|verification|audit|fix|robustness|blindspot|annotate|parent-context|citation-version|knowledge-update))?$/;

/** Knowledge update carries a second, closed segment. A closed enum keeps `#/lab/knowledge-update/unknown` unresolved. */
const KU_HASH = /^#\/lab(?:\/(demo))?\/knowledge-update(?:\/(sources|items|scan|revise|review|export))?$/;

export function parseLabHash(hash: string): { target: LabTarget; view?: LabView; step?: KUStep } | null {
  const knowledge = KU_HASH.exec(hash);
  if (knowledge) {
    const step = knowledge[2] as KUStep | undefined;
    // Spread instead of `step: undefined` so the shape stays free of empty keys for callers comparing objects.
    return { target: knowledge[1] ? 'demo' : 'lab', view: 'knowledge-update', ...(step ? { step } : {}) };
  }
  const match = LAB_HASH.exec(hash);
  if (!match) return null;
  const view = match[2] as LabView | undefined;
  return { target: match[1] ? 'demo' : view === 'workspace' ? 'workspace' : 'lab', view };
}

export function labHash(view: LabView, demo: boolean, step?: KUStep): string {
  return `#/lab${demo ? '/demo' : ''}/${view}${step ? `/${step}` : ''}`;
}
