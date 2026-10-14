import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Icon } from '../components/Icon';
import { REPO_URL } from '../config';
import packageData from '../fixtures/judge-annotation.json';
import './gate-audit.css';
import './annotate.css';

// Generated from the frozen evidence by tools/competition/judge_calibration.py prepare; it carries no judge verdicts.
const pkg = packageData;
export type Label = (typeof pkg.labels)[number]['value'];
export type Entry = { label?: Label; fromQuestion?: boolean; note?: string };
export type AnnotationState = { annotator: string; notAuthor: boolean; labels: Record<string, Entry> };

const STORAGE_KEY = `veriscope.judge-annotation.${pkg.packageId}`;
const VALUES = pkg.labels.map((label) => label.value) as Label[];
const questions = new Map(pkg.questions.map((question) => [question.id, question]));
const EMPTY: AnnotationState = { annotator: '', notAuthor: false, labels: {} };

// 标签与规则中的术语按语义上色：支持为绿，矛盾为红，无依据为橙，无法判断为灰。
const TONES: Record<Label, string> = { 支持: 'support', 矛盾: 'contradict', 无依据: 'missing', 无法判断: 'unsure' };
const TERM_TONES: Record<string, string> = { ...TONES, 资料: 'source' };

function emphasize(text: string) {
  return text.split(/(「[^」]+」)/).map((part, index) => {
    const term = /^「([^」]+)」$/.exec(part)?.[1];
    return term ? <em key={index} className={`an-term an-term--${TERM_TONES[term] ?? 'plain'}`}>{part}</em> : part;
  });
}

const labeled = (state: AnnotationState) => pkg.items.filter((item) => state.labels[item.id]?.label).length;

function load(): AnnotationState {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? 'null') as AnnotationState | null;
    return saved && typeof saved === 'object' && saved.labels ? { ...EMPTY, ...saved } : EMPTY;
  } catch {
    return EMPTY;
  }
}

function save(state: AnnotationState) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // 本地存储不可用时只是不能自动保存，导出不受影响
  }
}

/** The export format read by tools/competition/judge_calibration.py analyze (schemas/judge-annotation.v1.schema.json). */
export function exportAnnotation(state: AnnotationState, savedAt: string) {
  const labels: Record<string, { label: Label; fromQuestion?: boolean; note?: string }> = {};
  for (const item of pkg.items) {
    const entry = state.labels[item.id];
    if (!entry?.label) continue;
    labels[item.id] = { label: entry.label, ...(entry.fromQuestion ? { fromQuestion: true } : {}), ...(entry.note?.trim() ? { note: entry.note.trim() } : {}) };
  }
  return { schema: 'judge-annotation.v1', packageId: pkg.packageId, annotator: state.annotator.trim(), notAuthor: state.notAuthor, savedAt, labels };
}

/** Restores an exported file; rejects files made for different statements. */
export function importAnnotation(text: string): AnnotationState {
  const data = JSON.parse(text) as { schema?: string; packageId?: string; annotator?: string; notAuthor?: boolean; labels?: Record<string, Entry> };
  if (data.schema !== 'judge-annotation.v1') throw new Error('不是本页导出的标注文件。');
  if (data.packageId !== pkg.packageId) throw new Error('这份文件标注的是另一批陈述，不能导入。');
  const labels: Record<string, Entry> = {};
  for (const item of pkg.items) {
    const entry = data.labels?.[item.id];
    if (entry && VALUES.includes(entry.label as Label)) labels[item.id] = { label: entry.label, fromQuestion: entry.fromQuestion === true, note: entry.note ?? '' };
  }
  return { annotator: data.annotator ?? '', notAuthor: data.notAuthor === true, labels };
}

function download(state: AnnotationState) {
  const body = JSON.stringify(exportAnnotation(state, new Date().toISOString()), null, 2);
  const url = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `judge-annotation-${state.annotator.trim() || 'unnamed'}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

export function AnnotateView() {
  const [state, setState] = useState<AnnotationState>(load);
  const [index, setIndex] = useState(() => Math.max(0, pkg.items.findIndex((item) => !load().labels[item.id]?.label)));
  const [message, setMessage] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const item = pkg.items[index];
  const question = questions.get(item.question)!;
  const entry = state.labels[item.id] ?? {};
  const done = labeled(state);

  useEffect(() => save(state), [state]);

  const update = (id: string, change: Entry) => setState((current) => ({ ...current, labels: { ...current.labels, [id]: { ...current.labels[id], ...change } } }));
  const go = (next: number) => setIndex(Math.min(pkg.items.length - 1, Math.max(0, next)));
  const choose = (label: Label) => {
    update(item.id, { label });
    if (index < pkg.items.length - 1) go(index + 1);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') || event.ctrlKey || event.metaKey || event.altKey) return;
      const label = pkg.labels.find((option) => option.key === event.key);
      if (label) { event.preventDefault(); choose(label.value as Label); }
      else if (event.key === 'ArrowRight') go(index + 1);
      else if (event.key === 'ArrowLeft') go(index - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const onImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      const restored = importAnnotation(await file.text());
      setState(restored);
      setMessage(`已导入 ${labeled(restored)} 条标注。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '导入失败。');
    }
  };
  const onExport = () => {
    if (!state.annotator.trim()) { setMessage('请先填写标注人代号。'); return; }
    if (!state.notAuthor) { setMessage('请先确认未参与本项目的开发与题目编写。'); return; }
    download(state);
    setMessage(done < pkg.items.length ? `已导出 ${done} 条；还有 ${pkg.items.length - done} 条未标。未标完时，分析只使用从第一题起连续标完的部分。` : '已导出全部 52 条。请把文件发给作者。');
  };

  return <div className="audit-view an-view">
    <section className="audit-panel" aria-labelledby="an-intro-title">
      <div className="audit-panel-head"><div><h2 id="an-intro-title">逐条判断：这条陈述能否由资料推出</h2>
        <p>{`门禁的忠实度来自裁判对 ${pkg.items.length} 条陈述的逐条判定。请你不看裁判的结论，独立判断每条陈述，用来估计裁判判错的程度。约需 30 分钟，进度自动保存在本浏览器中。`}</p></div></div>
      <div className="an-intro">
        <section className="an-rules-block" aria-labelledby="an-rules-title">
          <h3 id="an-rules-title">标注规则<span>{`共 ${pkg.rules.length} 条`}</span></h3>
          <p>每条陈述都按下面的规则判断，拿不准时回来对照。</p>
          <ol className="an-rules">{pkg.rules.map((rule) => <li key={rule}>{emphasize(rule)}</li>)}</ol>
        </section>
        <div className="an-identity">
          <label><span>标注人代号</span><input value={state.annotator} maxLength={40} placeholder="例如 A，不必填真名" onChange={(event) => setState({ ...state, annotator: event.target.value })}/></label>
          <label className="an-check"><input type="checkbox" checked={state.notAuthor} onChange={(event) => setState({ ...state, notAuthor: event.target.checked })}/><span>我没有参与本项目的开发，也没有编写题目和参考答案</span></label>
          <a className="audit-source" href={`${REPO_URL}/blob/master/${pkg.guide}`} target="_blank" rel="noopener noreferrer"><Icon name="file" size={13}/>标注说明</a>
        </div>
      </div>
    </section>

    <section className="audit-panel" aria-labelledby="an-item-title">
      <div className="audit-panel-head">
        <div><h2 id="an-item-title">{`${item.id} · ${index + 1} / ${pkg.items.length}`}</h2><p>按数字键 1–4 选择并跳到下一条，← → 切换。</p></div>
        <span className={`audit-chip audit-chip--${done === pkg.items.length ? 'success' : 'warning'}`}>{`已标 ${done} / ${pkg.items.length}`}</span>
      </div>
      <div className="an-item">
        <div className="an-context">
          <span className="audit-kicker an-kicker an-kicker--question">题目（仅帮助理解，不作依据）</span>
          <p className="an-question">{question.question}</p>
          <span className="audit-kicker an-kicker an-kicker--source">资料</span>
          <ol className="an-passages">{question.contexts.map((context, position) => <li key={position}>{context}</li>)}</ol>
        </div>
        <div className="an-judge">
          <span className="audit-kicker an-kicker an-kicker--statement">陈述</span>
          <blockquote className="an-statement">{item.statement}</blockquote>
          {'note' in item && item.note && <p className="audit-note">{item.note}</p>}
          <div className="an-labels" role="group" aria-label="判断">
            {pkg.labels.map((option) => <button key={option.value} type="button" className={`an-label an-label--${TONES[option.value as Label]}${entry.label === option.value ? ' is-selected' : ''}`} aria-pressed={entry.label === option.value} onClick={() => choose(option.value as Label)}>
              <kbd>{option.key}</kbd><strong>{option.value}</strong><small>{option.hint}</small>
            </button>)}
          </div>
          <label className="an-check"><input type="checkbox" checked={entry.fromQuestion === true} onChange={(event) => update(item.id, { fromQuestion: event.target.checked })}/><span>内容来自题目</span></label>
          <label className="an-note"><span>备注（可选）</span><textarea rows={2} maxLength={500} value={entry.note ?? ''} onChange={(event) => update(item.id, { note: event.target.value })}/></label>
          <div className="an-nav">
            <button type="button" className="btn btn-secondary" onClick={() => go(index - 1)} disabled={index === 0}>上一条</button>
            <button type="button" className="btn btn-secondary" onClick={() => go(index + 1)} disabled={index === pkg.items.length - 1}>下一条</button>
          </div>
        </div>
      </div>
      <nav className="an-grid" aria-label="全部陈述">{pkg.items.map((other, position) => <button key={other.id} type="button" aria-current={position === index ? 'true' : undefined} className={`an-cell${state.labels[other.id]?.label ? ' is-done' : ''}`} title={state.labels[other.id]?.label ?? '未标'} onClick={() => go(position)}>{other.id.slice(1)}</button>)}</nav>
    </section>

    <section className="audit-panel" aria-labelledby="an-export-title">
      <div className="audit-panel-head"><div><h2 id="an-export-title">导出与交回</h2>
        <p>标完后导出文件交给作者；换浏览器继续时可先导入。两位标注人请各自独立完成，不要互相讨论。</p>
        <p>需要分几次完成时，请从第一条起按顺序往下标：分析只使用从第一题起连续标完的题，且至少 10 题；跳着标的题不计入。</p></div></div>
      <div className="an-nav">
        <button type="button" className="btn btn-primary" onClick={onExport}>导出标注文件</button>
        <button type="button" className="btn btn-secondary" onClick={() => fileInput.current?.click()}>导入</button>
        <input ref={fileInput} type="file" accept="application/json,.json" hidden aria-label="选择标注文件" onChange={onImport}/>
      </div>
      {message && <p className="audit-note" role="status">{message}</p>}
    </section>
  </div>;
}
