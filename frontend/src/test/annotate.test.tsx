import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { AnnotateView, exportAnnotation, importAnnotation } from '../features/AnnotateView';
import { parseLabHash } from '../labRoute';
import data from '../fixtures/judge-annotation.json';

const STORAGE_KEY = `veriscope.judge-annotation.${data.packageId}`;
const heading = () => screen.getByRole('heading', { level: 2, name: /^S\d{2} · / });

describe('Judge annotation view', () => {
  beforeEach(() => window.localStorage.clear());

  it('shows the first statement with its passages and no judge verdict', () => {
    render(<AnnotateView/>);
    expect(heading()).toHaveTextContent('S01 · 1 / 52');
    const first = data.items[0];
    const question = data.questions.find((q) => q.id === first.question)!;
    expect(screen.getByText(first.statement)).toBeInTheDocument();
    expect(screen.getAllByRole('listitem').filter((li) => question.contexts.includes(li.textContent ?? ''))).toHaveLength(question.contexts.length);
    expect(screen.getByText('已标 0 / 52')).toBeInTheDocument();
    expect(JSON.stringify(data)).not.toMatch(/verdict|caseId|rowIndex/);
    expect(screen.getByText(/分析只使用从第一题起连续标完的题，且至少 10 题/)).toBeInTheDocument();
  });

  it('labels with buttons and number keys, advances, and saves locally', () => {
    render(<AnnotateView/>);
    fireEvent.click(screen.getByRole('button', { name: /^1支持/ }));
    expect(heading()).toHaveTextContent('S02 · 2 / 52');
    fireEvent.keyDown(window, { key: '3' });
    expect(heading()).toHaveTextContent('S03 · 3 / 52');
    expect(screen.getByText('已标 2 / 52')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(within(screen.getByRole('group', { name: '判断' })).getByRole('button', { pressed: true })).toHaveTextContent('无依据');
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY)!);
    expect(saved.labels.S01.label).toBe('支持');
    expect(saved.labels.S02.label).toBe('无依据');
  });

  it('resumes from local storage at the first unlabeled statement', () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ annotator: 'A', notAuthor: true, labels: { S01: { label: '矛盾' }, S02: { label: '支持' } } }));
    render(<AnnotateView/>);
    expect(heading()).toHaveTextContent('S03 · 3 / 52');
    expect(screen.getByDisplayValue('A')).toBeInTheDocument();
  });

  it('requires an annotator code and the independence confirmation before export', () => {
    render(<AnnotateView/>);
    fireEvent.click(screen.getByRole('button', { name: '导出标注文件' }));
    expect(screen.getByRole('status')).toHaveTextContent('请先填写标注人代号');
    fireEvent.change(screen.getByPlaceholderText(/不必填真名/), { target: { value: 'B' } });
    fireEvent.click(screen.getByRole('button', { name: '导出标注文件' }));
    expect(screen.getByRole('status')).toHaveTextContent('请先确认');
  });

  it('exports the schema format and only re-imports files for the same statements', () => {
    const state = { annotator: ' A ', notAuthor: true, labels: { S01: { label: '支持' as const, fromQuestion: true, note: ' 见第五条 ' }, S02: { note: '没选标签' } } };
    const exported = exportAnnotation(state, '2026-10-14T08:00:00.000Z');
    expect(exported).toEqual({ schema: 'judge-annotation.v1', packageId: data.packageId, annotator: 'A', notAuthor: true, savedAt: '2026-10-14T08:00:00.000Z',
      labels: { S01: { label: '支持', fromQuestion: true, note: '见第五条' } } });
    const restored = importAnnotation(JSON.stringify(exported));
    expect(restored.labels.S01).toEqual({ label: '支持', fromQuestion: true, note: '见第五条' });
    expect(() => importAnnotation(JSON.stringify({ ...exported, packageId: '0'.repeat(64) }))).toThrow('另一批陈述');
    expect(() => importAnnotation('{}')).toThrow('不是本页导出');
  });

  it('is reachable by deep link', () => {
    expect(parseLabHash('#/lab/annotate')).toEqual({ target: 'lab', view: 'annotate' });
  });
});
