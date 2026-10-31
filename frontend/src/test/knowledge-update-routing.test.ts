import { describe, expect, it } from 'vitest';
import { KU_STEPS, labHash, parseLabHash } from '../labRoute';
import { KU_STEP_LABEL } from '../features/knowledge-update/useKuStep';

describe('knowledge update step routes', () => {
  it.each(KU_STEPS)('addresses the %s step', (step) => {
    expect(parseLabHash(`#/lab/knowledge-update/${step}`)).toEqual({ target: 'lab', view: 'knowledge-update', step });
    expect(parseLabHash(`#/lab/demo/knowledge-update/${step}`)).toEqual({ target: 'demo', view: 'knowledge-update', step });
    expect(labHash('knowledge-update', false, step)).toBe(`#/lab/knowledge-update/${step}`);
    expect(labHash('knowledge-update', true, step)).toBe(`#/lab/demo/knowledge-update/${step}`);
  });

  it('keeps the bare view link working without a step key', () => {
    const parsed = parseLabHash('#/lab/knowledge-update');
    expect(parsed).toEqual({ target: 'lab', view: 'knowledge-update' });
    expect(parsed).not.toHaveProperty('step');
    expect(labHash('knowledge-update', false)).toBe('#/lab/knowledge-update');
  });

  it('rejects a step outside the closed enum instead of treating it as a view', () => {
    expect(parseLabHash('#/lab/knowledge-update/unknown')).toBeNull();
    expect(parseLabHash('#/lab/knowledge-update/sources/extra')).toBeNull();
    expect(parseLabHash('#/lab/knowledge-update?step=sources')).toBeNull();
  });

  it('leaves the other workbench views untouched', () => {
    expect(parseLabHash('#/lab/audit')).toEqual({ target: 'lab', view: 'audit' });
    expect(parseLabHash('#/lab/demo/citation-version')).toEqual({ target: 'demo', view: 'citation-version' });
    expect(labHash('audit', false)).toBe('#/lab/audit');
  });

  it('labels every step', () => {
    expect(KU_STEPS.map((step) => KU_STEP_LABEL[step])).toEqual(['选来源与版本', '输入条目', '扫描差异', '人工修订', '复核', '下载']);
  });
});
