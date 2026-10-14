import type { View } from './api/types';

export type LabView = View | 'models' | 'verification' | 'audit' | 'fix' | 'robustness' | 'annotate';
export type LabTarget = 'lab' | 'demo' | 'workspace';

/** #/lab, #/lab/demo, #/lab/workspace keep their meaning; an optional trailing view segment deep-links a workbench view. */
const LAB_HASH = /^#\/lab(?:\/(demo))?(?:\/(overview|workspace|models|comparison|trace|report|verification|audit|fix|robustness|annotate))?$/;

export function parseLabHash(hash: string): { target: LabTarget; view?: LabView } | null {
  const match = LAB_HASH.exec(hash);
  if (!match) return null;
  const view = match[2] as LabView | undefined;
  return { target: match[1] ? 'demo' : view === 'workspace' ? 'workspace' : 'lab', view };
}

export function labHash(view: LabView, demo: boolean): string {
  return `#/lab${demo ? '/demo' : ''}/${view}`;
}
