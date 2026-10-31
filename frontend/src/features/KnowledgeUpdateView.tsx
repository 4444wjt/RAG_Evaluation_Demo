import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { STATIC_SITE } from '../config';
import { KUError, exportKU, fetchKUSnapshot, fetchKUSources, fetchKUVersions, lineCitation, logicalLines, makeKUInput, runKU, saveKUZip } from '../api/knowledgeUpdate';
import type { KUBundle, KUCitation, KUInput, KUItemInput, KUMode, KUSide, KUSnapshotInput, KUSource, KUSourceInput, KUVersion } from '../api/knowledgeUpdate';
import { KU_STEPS, type KUStep } from '../labRoute';
import { KU_STEP_LABEL, useKuStep } from './knowledge-update/useKuStep';
import './gate-audit.css';
import './knowledge-update.css';

const emptySnapshot = (): KUSnapshotInput => ({ version: '', text: null });
const newItem = (key: number): KUItemInput => ({ key, item_id: `item-${key}`, question: '', old_draft: '', source_ids: [], old_citations: [], revision: { enabled: false, proposed_draft: '', citations: [], open_questions: '' } });
const asError = (error: unknown) => error instanceof KUError ? error : new KUError(error instanceof Error ? error.message : '操作未完成，请重试。');
const display = (value: unknown) => value === null || value === undefined ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value);

function Failure({ error }: { error: KUError | null }) {
  if (!error) return null;
  return <div className="ku-error" role="alert"><strong>{error.message}</strong>{error.code && <p><code>{error.code}</code>{error.status ? ` · HTTP ${error.status}` : ''}</p>}{error.diagnostic !== undefined && <details open={error.status === 422}><summary>完整诊断</summary><pre>{JSON.stringify(error.diagnostic, null, 2)}</pre></details>}</div>;
}

/** A count that replays a short tick when its value changes, so a rerun reads as "new numbers" rather than the same table. */
function Count({ label, value }: { label: string; value: string | number }) {
  return <div><dt>{label}</dt><dd className="ku-count-value" key={String(value)}>{value}</dd></div>;
}

/**
 * Unified diff with the usual add/remove/hunk tint. Each logical line becomes its own block so the
 * background can span the row, while the newline stays in the DOM (hidden) — that keeps textContent,
 * copy and the core's exact diff text identical to what the run returned.
 */
function DiffBlock({ text, label }: { text: string; label: string }) {
  const lines = text.split('\n');
  const kindOf = (line: string) => line.startsWith('+++') || line.startsWith('---') ? 'meta'
    : line.startsWith('@@') ? 'hunk'
    : line.startsWith('+') ? 'add'
    : line.startsWith('-') ? 'del'
    : 'ctx';
  return <pre className="ku-diff" tabIndex={0} aria-label={label}>
    {lines.map((line, index) => <span className="ku-diff-line" data-kind={kindOf(line)} key={index}>{line}{index < lines.length - 1 && <span className="ku-diff-eol">{'\n'}</span>}</span>)}
  </pre>;
}

/** Snapshot summary is always visible: the byte binding is the argument, not a detail to hide. */
function SnapshotFacts({ snapshot }: { snapshot: NonNullable<KUSnapshotInput['snapshot']> }) {
  return <dl className="ku-facts">
    <div><dt>采集时间</dt><dd>{snapshot.captured_at}（非文档发布日期）</dd></div>
    <div><dt>字节 / 行</dt><dd>{snapshot.size} B · {logicalLines(snapshot.text).length} 逻辑行</dd></div>
    <div><dt>SHA-256</dt><dd title={snapshot.sha256}>{snapshot.sha256.slice(0, 12)}…</dd></div>
  </dl>;
}

function SnapshotPicker({ source, side, value, versions, onChange }: { source: KUSource; side: KUSide; value: KUSnapshotInput; versions: KUVersion[]; onChange: (next: KUSnapshotInput) => void }) {
  const id = useId();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<KUError | null>(null);
  const [retry, setRetry] = useState(0);
  const label = side === 'old' ? '改动前快照' : '改动后快照';
  // Each selection has its own cancellation scope. A late snapshot cannot replace another selection.
  useEffect(() => {
    setError(null); setLoading(false);
    if (!/^[0-9]{1,12}$/.test(value.version)) return;
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    fetchKUSnapshot(source.source_id, value.version, controller.signal).then((snapshot) => {
      if (alive) onChange({ version: value.version, text: snapshot.text, snapshot });
    }).catch((problem) => { if (alive) setError(asError(problem)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [source.source_id, value.version, retry]);
  return <div className="ku-snapshot">
    <label className="ku-field" htmlFor={id}><span>{source.title} · {label}</span><select id={id} value={value.version} onChange={(event) => { setError(null); onChange({ version: event.target.value, text: event.target.value === 'manual' ? '' : null }); }}>
      <option value="">请选择{label}</option><option value="none">{side === 'old' ? '无 · 新增来源' : '无 · 删除来源'}</option><option value="manual">手动粘贴文本</option>
      {versions.map((version) => <option key={version.timestamp} value={version.timestamp}>{`${version.timestamp} · 采集于 ${version.captured_at}`}</option>)}
    </select></label>
    {value.version === 'manual' && <label className="ku-field"><span>{source.title} · {side === 'old' ? '改动前' : '改动后'}文本</span><textarea aria-label={`${source.title} · ${side === 'old' ? '改动前' : '改动后'}文本`} rows={7} value={value.text ?? ''} onChange={(event) => onChange({ version: 'manual', text: event.target.value })} spellCheck={false}/><small>手动文本使用 manual 版本标签；不表示已由采集服务取得。</small></label>}
    {loading && <p className="ku-loading" role="status">正在读取{label}…</p>}
    <Failure error={error}/>
    {error && <button className="btn btn-secondary" type="button" onClick={() => setRetry((n) => n + 1)}>重试读取{label}</button>}
    {value.snapshot && <SnapshotFacts snapshot={value.snapshot}/>}
  </div>;
}

/** What one side picked, in the same terms the picker uses: empty, explicit absence, pasted text, or a captured moment. */
function sideLabel(value: KUSnapshotInput) {
  if (value.version === '') return '未选择';
  if (value.version === 'none') return '无（新增或删除）';
  if (value.version === 'manual') return '手动粘贴';
  return value.snapshot?.captured_at ?? value.version;
}

/**
 * Read-only roll-up of the selected sources: both sides of every source on one line, so the reader
 * sees what is still missing without opening each card. Readiness comes from the same `unready`
 * pass that blocks the run — this table never judges on its own.
 */
function SourceSummary({ rows, unreadySides }: { rows: KUSourceInput[]; unreadySides: Set<string> }) {
  if (!rows.length) return null;
  return <div className="ku-summary">
    <div className="ku-summary-scroll">
      <table className="ku-summary-table">
        <caption>已选版本汇总 · 只读</caption>
        <thead><tr><th scope="col">来源</th><th scope="col">改动前</th><th scope="col">改动后</th><th scope="col">状态</th></tr></thead>
        <tbody>{rows.map((row) => {
          const id = row.source.source_id;
          const ready = !unreadySides.has(`${id}:old`) && !unreadySides.has(`${id}:new`);
          return <tr key={id}>
            <th scope="row"><span className="ku-summary-source">{row.source.title}</span><code>{id}</code></th>
            <td data-label="改动前">{sideLabel(row.old)}</td>
            <td data-label="改动后">{sideLabel(row.new)}</td>
            <td data-label="状态"><span className={ready ? 'ku-summary-ready' : 'ku-summary-unready'}>{ready ? '就绪' : '未就绪'}</span></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  </div>;
}

function SourceEditor({ row, versions, loading, error, onRetry, onChange, onRemove }: { row: KUSourceInput; versions: KUVersion[]; loading: boolean; error: KUError | null; onRetry: () => void; onChange: (side: KUSide, value: KUSnapshotInput) => void; onRemove: () => void }) {
  return <article className="ku-source" aria-label={`来源 ${row.source.source_id}`}>
    <div className="ku-section-head"><div><h3>{row.source.title}</h3><p className="ku-note"><code>{row.source.source_id}</code> · <a href={row.source.url} target="_blank" rel="noopener noreferrer">查看审核来源</a></p></div><button className="btn btn-ghost" type="button" onClick={onRemove}>移除来源</button></div>
    {loading && <p className="ku-loading" role="status">正在读取历史版本…</p>}
    <Failure error={error}/>
    {error?.code === 'COLLECTOR_ERROR' && <p className="ku-note">采集服务读取失败时仍可继续：在改动前、改动后两处都选“手动粘贴文本”。</p>}
    {error && <button className="btn btn-secondary" type="button" onClick={onRetry}>重试版本列表</button>}
    {(!row.source.collector_ready || error?.status === 503) && <p className="ku-manual-note">未配置采集：请在改动前、改动后两处都选“手动粘贴文本”。</p>}
    {row.source.collector_ready && !loading && !error && versions.length === 0 && <p className="ku-empty">历史版本为空，没有可选的采集快照。可手动粘贴文本。</p>}
    {/* 采集只在来源真的变更时才新增版本，首次接入通常只有 1 条；说清楚这点，读者才不会以为下拉坏了 */}
    {row.source.collector_ready && !loading && !error && versions.length === 1 && <p className="ku-note">该来源目前只有 1 个采集版本：另一侧请选「无」（新增或删除）或手动粘贴文本，才有可比的差异。</p>}
    <p className="ku-hint">同一个来源要比两份固定快照：<strong>改动前</strong>是基线，<strong>改动后</strong>是这次要检查的文本，核心只比这两份之间的字面差异。任一侧选「无」，表示这个来源是新增或删除。对应 task.json 的 old / new。</p>
    <div className="ku-columns">{(['old', 'new'] as const).map((side) => <SnapshotPicker key={side} source={row.source} side={side} value={row[side]} versions={versions} onChange={(value) => onChange(side, value)}/>)}</div>
    {row.old.version !== '' && row.old.version !== 'none' && row.old.version !== 'manual' && row.old.version === row.new.version && <p className="ku-note">两侧选了同一份快照，差异必然为空；要对比改动，请选不同的采集时间，或改用手动粘贴。</p>}
    {row.old.version === 'none' && row.new.version === 'none' && <p className="ku-error" role="alert">改动前、改动后不能同时为无。</p>}
  </article>;
}

function LinePicker({ sources, side, onAdd }: { sources: KUSourceInput[]; side: KUSide; onAdd: (citation: KUCitation) => void }) {
  const id = useId();
  // Steps remount their content, so the picker defaults to the first associated source instead of losing the choice.
  const [sourceId, setSourceId] = useState(() => sources[0]?.source.source_id ?? '');
  const [range, setRange] = useState<[number, number] | null>(null);
  const [anchor, setAnchor] = useState<number | null>(null);
  useEffect(() => {
    if (!sources.some((row) => row.source.source_id === sourceId)) setSourceId(sources[0]?.source.source_id ?? '');
  }, [sources, sourceId]);
  const source = sources.find((row) => row.source.source_id === sourceId);
  const value = source?.[side];
  const text = value?.text;
  useEffect(() => { setRange(null); setAnchor(null); }, [sourceId, text]);
  const lines = text === null || text === undefined ? [] : logicalLines(text);
  const choose = (line: number) => {
    if (anchor === null) { setAnchor(line); setRange([line, line]); }
    else { setRange([Math.min(anchor, line), Math.max(anchor, line)]); setAnchor(null); }
  };
  return <div className="ku-line-picker">
    <label className="ku-field" htmlFor={id}><span>{side === 'old' ? '改动前文本引文来源' : '改动后文本引文来源'}</span><select id={id} value={sourceId} onChange={(event) => setSourceId(event.target.value)}><option value="">请选择已关联的来源</option>{sources.map((row) => <option key={row.source.source_id} value={row.source.source_id}>{row.source.title}</option>)}</select></label>
    {source && (value?.version === 'none' ? <p className="ku-note">该侧明确无版本，不能选取引文。</p> : text === null || text === undefined ? <p className="ku-note">请先选定并读取该侧文本。</p> : <>
      <p className="ku-note">点选一行；连续区间再点结束行。引文保留完整逻辑行与空白。</p>
      <div className="ku-lines" role="group" aria-label={`${source.source.title} ${side === 'old' ? '改动前' : '改动后'}文本行`}>
        {lines.map((line, index) => <button className="ku-line" key={index} type="button" aria-label={`第 ${index + 1} 行：${line || '空行'}`} aria-pressed={!!range && index + 1 >= range[0] && index + 1 <= range[1]} onClick={() => choose(index + 1)}><span aria-hidden="true">{index + 1}</span><code>{line || '\u00a0'}</code></button>)}
      </div>
      <div className="ku-actions"><button className="btn btn-secondary" type="button" disabled={!range} onClick={() => { if (range) { onAdd(lineCitation(sourceId, text, ...range)); setRange(null); setAnchor(null); } }}>添加{range ? `第 ${range[0]}–${range[1]} 行` : '所选行'}引文</button><button className="btn btn-ghost" type="button" disabled={!range} onClick={() => { setRange(null); setAnchor(null); }}>清除选择</button></div>
    </>)}
  </div>;
}

function CitationList({ citations, onRemove }: { citations: KUCitation[]; onRemove: (index: number) => void }) {
  return citations.length ? <ul className="ku-citations">{citations.map((citation, index) => <li key={index}><div className="ku-section-head"><span><code>{citation.source_id}</code> · 第 {citation.line_start}–{citation.line_end} 行</span><button className="btn btn-ghost" type="button" aria-label={`移除引文 ${index + 1}`} onClick={() => onRemove(index)}>移除引文</button></div><pre>{citation.quote}</pre></li>)}</ul> : <p className="ku-note">尚未添加引文；缺失引文仍交给核心检查。</p>;
}

/** Step 2 keeps only what defines an item; revisions live on their own step so the two never write the same field. */
function ItemEditor({ item, index, sources, onChange, onRemove }: { item: KUItemInput; index: number; sources: KUSourceInput[]; onChange: (patch: Partial<KUItemInput>) => void; onRemove: () => void }) {
  const associated = sources.filter((row) => item.source_ids.includes(row.source.source_id));
  return <fieldset className="ku-item"><legend>条目 {index + 1}</legend>
    <div className="ku-section-head"><p className="ku-note">原稿、关联来源和引文均由你明确填写。</p><button type="button" className="btn btn-ghost" onClick={onRemove}>删除条目</button></div>
    <div className="ku-columns"><label className="ku-field"><span>条目 ID</span><input value={item.item_id} onChange={(event) => onChange({ item_id: event.target.value })}/></label><label className="ku-field"><span>问题（可选）</span><input value={item.question} onChange={(event) => onChange({ question: event.target.value })}/></label></div>
    <label className="ku-field"><span>原稿</span><textarea rows={4} value={item.old_draft} onChange={(event) => onChange({ old_draft: event.target.value })}/></label>
    <fieldset className="ku-associations"><legend>关联来源</legend>{sources.map((row) => <label className="ku-checkbox" key={row.source.source_id}><input type="checkbox" checked={item.source_ids.includes(row.source.source_id)} onChange={(event) => onChange({ source_ids: event.target.checked ? [...item.source_ids, row.source.source_id] : item.source_ids.filter((id) => id !== row.source.source_id) })}/>{row.source.title}</label>)}{!sources.length && <p className="ku-note">请先选择来源。</p>}
      {item.source_ids.filter((id) => !sources.some((row) => row.source.source_id === id)).map((id) => <label key={id} className="ku-checkbox"><input type="checkbox" checked onChange={() => onChange({ source_ids: item.source_ids.filter((sid) => sid !== id) })}/>{id}（来源已移除，请处理关联）</label>)}
    </fieldset>
    <section className="ku-citation-block"><h4>原稿引文 · 从改动前文本选行</h4><LinePicker sources={associated} side="old" onAdd={(citation) => onChange({ old_citations: [...item.old_citations, citation] })}/><CitationList citations={item.old_citations} onRemove={(i) => onChange({ old_citations: item.old_citations.filter((_, index) => index !== i) })}/></section>
  </fieldset>;
}

/** Step 4 only edits the revision; the original draft is read-only here so one field has one owner. */
function RevisionEditor({ item, index, sources, onChange }: { item: KUItemInput; index: number; sources: KUSourceInput[]; onChange: (patch: Partial<KUItemInput['revision']>) => void }) {
  const associated = sources.filter((row) => item.source_ids.includes(row.source.source_id));
  return <fieldset className="ku-item"><legend>条目 {index + 1} · <code>{item.item_id}</code></legend>
    <div className="ku-section-head"><p className="ku-note">原稿只读；要改原稿请回第 2 步。</p><span className="badge neutral">{item.revision.enabled ? '已提交修订' : '未提交'}</span></div>
    <div className="ku-readonly"><span>原稿</span><p>{item.old_draft || '（空）'}</p></div>
    <label className="ku-checkbox"><input type="checkbox" checked={item.revision.enabled} onChange={(event) => onChange({ enabled: event.target.checked })}/>提交本条修订</label>
    <p className="ku-note">未勾选的条目仍参与复核，不缩小分母。</p>
    {item.revision.enabled && <>
      <label className="ku-field"><span>修订稿 proposed_draft</span><textarea rows={4} value={item.revision.proposed_draft} onChange={(event) => onChange({ proposed_draft: event.target.value })}/></label>
      <LinePicker sources={associated} side="new" onAdd={(citation) => onChange({ citations: [...item.revision.citations, citation] })}/><CitationList citations={item.revision.citations} onRemove={(i) => onChange({ citations: item.revision.citations.filter((_, index) => index !== i) })}/>
      <label className="ku-field"><span>待确认问题（可选，每行一项）</span><textarea rows={2} value={item.revision.open_questions} onChange={(event) => onChange({ open_questions: event.target.value })}/></label>
    </>}
  </fieldset>;
}

function Results({ bundle }: { bundle: KUBundle }) {
  const report = bundle.report;
  return <div className="ku-results"><div className="ku-section-head"><h3>{report.mode === 'scan' ? '扫描结果' : '复核结果'}</h3><span className="badge neutral">本次接口结果 · {report.run_status}</span></div>
    <dl className="ku-counts">{[['declared_item_count', '声明条目'], ['result_item_count', '结果条目'], ['submitted_revision_count', '已修订'], ['unsubmitted_item_count', '未修订'], ['coverage_gap_count', '覆盖缺口'], ['input_error_count', '输入错误']].map(([key, label]) => <Count key={key} label={label} value={display(report.summary[key])}/>)}</dl>
    <h4>来源状态与差异</h4>{report.sources?.map((source, index) => <article className="ku-result-row" key={source.source_id ?? index} style={{ ['--r' as string]: index }}><h5><code>{source.source_id}</code> · {source.status}</h5>{source.diff === null ? <p className="ku-note">没有差异文本；请查看来源读取诊断，不表示来源已删除。</p> : source.diff === '' ? <p className="ku-note">核心返回的 unified diff 为空。</p> : <DiffBlock text={source.diff} label={`${source.source_id} unified diff`}/>}</article>)}
    <h4>逐条结果</h4>{report.items?.map((item, index) => <article className="ku-result-row" key={item.item_id ?? index} style={{ ['--r' as string]: index }}><h5>{item.item_id} · {item.status}</h5><p className="ku-note">修订状态：{item.revision_state} · 语义：{display(item.semantic_evaluation)} · 批准发布：{display(item.publication_approved)}</p><details className="ku-details"><summary>完整逐条结果</summary><pre tabIndex={0} aria-label={`${item.item_id} 完整逐条结果`}>{JSON.stringify(item, null, 2)}</pre></details></article>)}
    <p className="ku-boundary">MATCHED 只表示字面匹配；语义始终为 NOT_EVALUATED，本工具不批准发布。</p>
    <details className="ku-details"><summary>完整报告、分母与运行绑定</summary><pre>{JSON.stringify(bundle, null, 2)}</pre></details>
  </div>;
}

type Current = { bundle: KUBundle; input: KUInput; mode: KUMode };

export function KnowledgeUpdateView() {
  const { step, direction, go } = useKuStep(false);
  const [catalogue, setCatalogue] = useState<KUSource[]>([]);
  const [loading, setLoading] = useState(!STATIC_SITE);
  const [catalogueError, setCatalogueError] = useState<KUError | null>(null);
  const [reload, setReload] = useState(0);
  const [sources, setSources] = useState<KUSourceInput[]>([]);
  const [taskId, setTaskId] = useState('knowledge-update');
  const [items, setItems] = useState<KUItemInput[]>([newItem(1)]);
  const counter = useRef(1);
  const [phase, setPhase] = useState<KUMode | 'export' | null>(null);
  const [failure, setFailure] = useState<KUError | null>(null);
  const [current, setCurrent] = useState<Current | null>(null);
  const currentRef = useRef<Current | null>(null);
  const [stale, setStale] = useState(false);
  const [downloadNote, setDownloadNote] = useState('');
  const [undo, setUndo] = useState<{ id: number; message: string; run: () => void } | null>(null);
  const undoTimer = useRef<number | null>(null);
  const [versions, setVersions] = useState<Record<string, KUVersion[]>>({});
  const [versionError, setVersionError] = useState<Record<string, KUError | null>>({});
  const [versionLoading, setVersionLoading] = useState<Record<string, boolean>>({});
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);

  useEffect(() => {
    if (STATIC_SITE) return;
    let alive = true;
    const controller = new AbortController();
    setLoading(true); setCatalogueError(null);
    fetchKUSources(controller.signal).then((result) => { if (alive) setCatalogue(result); }).catch((error) => { if (alive) setCatalogueError(asError(error)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [reload]);

  // Version lists are cached in the shell: switching steps must not refetch and flash a loading state.
  useEffect(() => {
    if (STATIC_SITE) return;
    for (const row of sources) {
      const id = row.source.source_id;
      if (!row.source.collector_ready || versions[id]) continue;
      const controller = new AbortController();
      setVersionLoading((state) => ({ ...state, [id]: true }));
      fetchKUVersions(id, controller.signal).then((result) => { setVersions((state) => ({ ...state, [id]: result })); })
        .catch((error) => { setVersionError((state) => ({ ...state, [id]: asError(error) })); })
        .finally(() => { setVersionLoading((state) => ({ ...state, [id]: false })); });
    }
  }, [sources, versions]);

  useEffect(() => () => { generation.current += 1; active.current?.abort(); }, []);
  useEffect(() => () => { if (undoTimer.current) window.clearTimeout(undoTimer.current); }, []);

  // Moving steps must announce the new page: focus its heading instead of leaving focus on the rail.
  // Compare against the last announced step rather than a "first render" flag — React's dev double-mount
  // re-runs effects with a fresh ref, so a flag would be false on the second mount and steal focus on load.
  const announced = useRef(step);
  useEffect(() => {
    if (announced.current === step) return;
    announced.current = step;
    const heading = document.getElementById(`ku-title-${step}`);
    if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
  }, [step]);

  const retire = () => {
    generation.current += 1; active.current?.abort(); active.current = null;
    setPhase(null); setFailure(null); setDownloadNote('');
    if (currentRef.current) { currentRef.current = null; setCurrent(null); setStale(true); }
  };
  const changeSource = (sourceId: string, side: KUSide, value: KUSnapshotInput) => {
    // A remounted picker re-emits the snapshot it just read; that is the same input, not an edit, so results stay valid.
    const previous = sources.find((row) => row.source.source_id === sourceId)?.[side];
    if (previous && previous.version === value.version && previous.text === value.text) return;
    retire(); setSources((rows) => rows.map((row) => row.source.source_id === sourceId ? { ...row, [side]: value } : row));
  };
  // One removal, one reversible entry. It expires after 9s so the strip cannot become a second state store.
  const offerUndo = (message: string, run: () => void) => {
    if (undoTimer.current) window.clearTimeout(undoTimer.current);
    const entry = { id: Date.now(), message, run };
    setUndo(entry);
    undoTimer.current = window.setTimeout(() => setUndo((open) => open?.id === entry.id ? null : open), 9000);
  };
  const removeSource = (row: KUSourceInput) => {
    retire();
    const at = sources.findIndex((entry) => entry.source.source_id === row.source.source_id);
    setSources((rows) => rows.filter((entry) => entry.source.source_id !== row.source.source_id));
    offerUndo('已移除来源；指向它的条目关联会标为待处理。', () => {
      retire();
      setSources((rows) => {
        if (rows.some((entry) => entry.source.source_id === row.source.source_id)) return rows;
        const next = [...rows]; next.splice(Math.min(Math.max(at, 0), next.length), 0, row); return next;
      });
    });
  };
  const removeItem = (item: KUItemInput) => {
    retire();
    const at = items.findIndex((entry) => entry.key === item.key);
    setItems((rows) => rows.filter((entry) => entry.key !== item.key));
    // The entry carries its citations, so losing it silently is the one deletion users cannot rebuild from memory.
    offerUndo('已删除条目，含它的原稿引文。', () => {
      retire();
      setItems((rows) => {
        if (rows.some((entry) => entry.key === item.key)) return rows;
        const next = [...rows]; next.splice(Math.min(Math.max(at, 0), next.length), 0, item); return next;
      });
    });
  };
  const execute = async (mode: KUMode) => {
    retire();
    const token = generation.current;
    const controller = new AbortController(); active.current = controller;
    setPhase(mode);
    try {
      const input = makeKUInput(taskId, sources, items, mode);
      const bundle = await runKU(mode, input, controller.signal);
      if (generation.current !== token) return;
      const result = { bundle, input, mode };
      currentRef.current = result; setCurrent(result); setStale(false);
      // Results get their own step, so a run lands the reader on the output instead of leaving them on the input page.
      if (mode === 'recheck') go('review'); else go('scan');
    } catch (error) { if (generation.current === token) setFailure(asError(error)); }
    finally { if (generation.current === token) { setPhase(null); active.current = null; } }
  };
  const download = async () => {
    const result = currentRef.current;
    if (!result) return;
    const token = ++generation.current;
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setPhase('export'); setFailure(null); setDownloadNote('');
    try {
      const zip = await exportKU(result.mode, result.input, controller.signal);
      if (generation.current !== token || currentRef.current !== result) return;
      saveKUZip(zip.blob, zip.filename); setDownloadNote('已生成 ZIP 下载，请在浏览器下载列表中查看。');
    } catch (error) { if (generation.current === token) setFailure(asError(error)); }
    finally { if (generation.current === token) { setPhase(null); active.current = null; } }
  };

  if (STATIC_SITE) return <div className="ku-view"><section className="audit-panel"><h2>知识更新工作页</h2><p>静态站只提供入口说明，没有后端服务。请在开启知识更新接口的站点选择来源与版本、填写条目、人工修订并下载复核包。</p><p>MATCHED 只表示完整行的字面匹配；语义始终为 NOT_EVALUATED，本工具不批准发布。</p></section></div>;

  const unready: string[] = [];
  const unreadySides = new Set<string>();
  for (const row of sources) {
    for (const side of ['old', 'new'] as const) {
      const value = row[side];
      const label = `${row.source.title} · ${side === 'old' ? '改动前' : '改动后'}快照尚未就绪`;
      if (value.version === '') { unready.push(`${label}（未选择）`); unreadySides.add(`${row.source.source_id}:${side}`); }
      else if (value.version !== 'none' && value.text === null) { unready.push(`${label}（未读取）`); unreadySides.add(`${row.source.source_id}:${side}`); }
    }
  }
  // Completion is derived from real input, not from "the user walked past this step".
  const completed: Record<KUStep, boolean> = {
    sources: sources.length > 0 && unready.length === 0,
    items: items.length > 0 && items.every((item) => item.old_draft.trim() !== '' && item.source_ids.length > 0),
    scan: !!current && current.mode === 'scan',
    revise: items.some((item) => item.revision.enabled),
    review: !!current && current.mode === 'recheck',
    export: false,
  };
  const blocked = unready.length > 0;
  const position = KU_STEPS.indexOf(step);
  const stepId = (entry: KUStep) => `ku-step-${entry}`;
  const titleId = (entry: KUStep) => `ku-title-${entry}`;
  const moveRail = (event: ReactKeyboardEvent<HTMLElement>) => {
    const next = event.key === 'ArrowRight' ? Math.min(position + 1, KU_STEPS.length - 1)
      : event.key === 'ArrowLeft' ? Math.max(position - 1, 0)
      : event.key === 'Home' ? 0
      : event.key === 'End' ? KU_STEPS.length - 1
      : -1;
    if (next < 0) return;
    event.preventDefault();
    const target = KU_STEPS[next];
    go(target);
    window.requestAnimationFrame(() => document.getElementById(stepId(target))?.focus());
  };

  return <div className="ku-view" aria-busy={!!phase}>
    <nav className="ku-rail" aria-label="知识更新步骤" onKeyDown={moveRail}>
      <ol>{KU_STEPS.map((entry: KUStep, index) => <li key={entry}>
        <button type="button" id={stepId(entry)} className="ku-step" data-step={entry} data-state={entry === step ? 'current' : completed[entry] ? 'done' : 'todo'} aria-label={KU_STEP_LABEL[entry]} aria-current={entry === step ? 'step' : undefined} tabIndex={entry === step ? 0 : -1} onClick={() => go(entry)}>
          <span className="ku-step-dot" aria-hidden="true">{completed[entry] && entry !== step ? '✓' : index + 1}</span><span className="ku-step-label">{KU_STEP_LABEL[entry]}</span>
        </button>
      </li>)}</ol>
    </nav>

    <div className={`ku-step-body ku-step-${step}`} data-dir={direction} key={step}>
      {step === 'sources' && <section className="ku-panel-stack" aria-labelledby="ku-title-sources">
        <h2 id="ku-title-sources" className="ku-step-title">来源与固定版本</h2>
        <p className="ku-boundary">来源由维护者预置，不在页面开放任意网址。明确选择改动前后的两份快照；读取失败不当作删除。</p>
        {loading && <p className="ku-loading" role="status">正在读取审核来源…</p>}<Failure error={catalogueError}/>{catalogueError && <button className="btn btn-secondary" type="button" onClick={() => setReload((n) => n + 1)}>重试来源清单</button>}
        {!loading && !catalogueError && !catalogue.length && <p className="ku-empty">审核来源清单为空。</p>}
        <dl className="ku-counts">
          <div><dt>已选来源</dt><dd>{sources.length}</dd></div>
          {/* Numerator and denominator must be the same unit: both count sides (2 per source), not sources. */}
          <div><dt>版本就绪</dt><dd>{sources.length ? <>{sources.length * 2 - unready.length}<small>/ {sources.length * 2}</small></> : '—'}</dd></div>
          <div><dt>未配置采集</dt><dd>{catalogue.filter((source) => !source.collector_ready).length}</dd></div>
          <div><dt>清单来源</dt><dd>{catalogue.length}</dd></div>
        </dl>
        <div className="ku-source-options">{catalogue.map((source, index) => {
          const picked = sources.some((row) => row.source.source_id === source.source_id);
          const count = versions[source.source_id]?.length;
          return <label className="ku-source-option" key={source.source_id} style={{ ['--ku-i' as string]: index }}>
            <input type="checkbox" checked={picked} onChange={(event) => { retire(); setSources((rows) => event.target.checked ? [...rows, { source, old: emptySnapshot(), new: emptySnapshot() }] : rows.filter((row) => row.source.source_id !== source.source_id)); }}/>
            <span className="ku-source-option-top"><span className="ku-source-option-mark" aria-hidden="true">{picked ? '✓' : ''}</span><span className="ku-source-option-title">{source.title}</span></span>
            <span className="ku-source-option-id">{source.source_id}</span>
            <span className="ku-source-option-row">
              <span className={`audit-chip ${source.collector_ready ? 'audit-chip--success' : 'audit-chip--warning'}`} title={source.collector_ready ? '清单已为该来源配置采集，但这不代表采集服务此刻在线；读取失败会以错误形式显示，不会当作没有版本。' : '清单未配置该来源的采集，只能手动粘贴文本。'}>{source.collector_ready ? '已配置采集' : '未配置采集'}</span>
              <span className="ku-note">{source.collector_ready ? (picked ? (count === undefined ? '读取版本…' : `${count} 个历史版本`) : '选中后读取版本') : '需手动粘贴'}</span>
            </span>
            <span className="ku-source-option-meta">{source.extraction.fetch_backend} · filters {source.extraction.include_filters.length}</span>
          </label>;
        }        )}</div>
        <SourceSummary rows={sources} unreadySides={unreadySides}/>
        {sources.map((row) => <SourceEditor key={row.source.source_id} row={row} versions={versions[row.source.source_id] ?? []} loading={!!versionLoading[row.source.source_id]} error={versionError[row.source.source_id] ?? null} onRetry={() => { setVersions((state) => { const next = { ...state }; delete next[row.source.source_id]; return next; }); setVersionError((state) => ({ ...state, [row.source.source_id]: null })); }} onChange={(side, value) => changeSource(row.source.source_id, side, value)} onRemove={() => removeSource(row)}/>)}
      </section>}

      {step === 'items' && <section className="ku-panel-stack" aria-labelledby="ku-title-items">
        <h2 id="ku-title-items" className="ku-step-title">条目与引文</h2>
        <p className="ku-boundary">每条都要声明原稿、关联来源与改动前文本引文。未填引文的条目仍会进入检查，不缩小分母。</p>
        <label className="ku-field ku-task-id"><span>任务 ID</span><input value={taskId} onChange={(event) => { retire(); setTaskId(event.target.value); }}/></label>
        {items.map((item, index) => <ItemEditor key={item.key} item={item} index={index} sources={sources} onChange={(patch) => { retire(); setItems((rows) => rows.map((row) => row.key === item.key ? { ...row, ...patch } : row)); }} onRemove={() => removeItem(item)}/>)}
        <div className="ku-actions"><button className="btn btn-secondary" type="button" onClick={() => { retire(); setItems((rows) => [...rows, newItem(++counter.current)]); }}>添加条目</button></div>
      </section>}

      {step === 'scan' && <section className="ku-panel-stack" aria-labelledby="ku-title-scan">
        <h2 id="ku-title-scan" className="ku-step-title">扫描差异</h2>
        <p className="ku-boundary">只比固定版本之间的差异，并对条目做字面引文定位。扫描不调用模型、不判语义。</p>
        {unready.length > 0 && <ul className="ku-checklist">{unready.map((line) => <li key={line}>{line}</li>)}</ul>}
        {current && current.mode === 'scan' ? <Results bundle={current.bundle}/> : !phase && <p className="ku-empty">尚无本次结果。歧义、未修订与失败项都会保留在核心输出中。</p>}
      </section>}

      {step === 'revise' && <section className="ku-panel-stack" aria-labelledby="ku-title-revise">
        <h2 id="ku-title-revise" className="ku-step-title">人工修订</h2>
        <p className="ku-boundary">只有勾选「提交本条修订」的条目才会带 proposed_draft；未勾选的仍参与复核。</p>
        <p className="ku-note">已提交修订 {items.filter((item) => item.revision.enabled).length} / 共 {items.length} 条。</p>
        {items.map((item, index) => <RevisionEditor key={item.key} item={item} index={index} sources={sources} onChange={(patch) => { retire(); setItems((rows) => rows.map((row) => row.key === item.key ? { ...row, revision: { ...row.revision, ...patch } } : row)); }}/>)}
      </section>}

      {step === 'review' && <section className="ku-panel-stack" aria-labelledby="ku-title-review">
        <h2 id="ku-title-review" className="ku-step-title">复核</h2>
        <p className="ku-boundary">复核重跑全部条目，包括未修订项。语义始终为 NOT_EVALUATED，本工具不批准发布。</p>
        {current && current.mode === 'recheck' ? <Results bundle={current.bundle}/> : !phase && <p className="ku-empty">尚未复核。点下方「复核 recheck」重跑全部条目；未修订项也会进入结果。</p>}
      </section>}

      {step === 'export' && <section className="ku-panel-stack" aria-labelledby="ku-title-export">
        <h2 id="ku-title-export" className="ku-step-title">下载</h2>
        <p className="ku-boundary">导出按当前输入重新计算打包，ZIP 内含报告、交接说明与运行绑定清单。</p>
        {current ? <>
          <dl className="ku-counts">{[['mode', '模式'], ['result_item_count', '结果条目'], ['coverage_gap_count', '覆盖缺口'], ['input_error_count', '输入错误']].map(([key, label]) => <Count key={key} label={label} value={key === 'mode' ? current.bundle.report.mode : display(current.bundle.report.summary[key])}/>)}</dl>
          <p className="ku-note">文件名由模式与结果哈希决定：<code>knowledge-update-{current.bundle.report.mode}-&lt;hash12&gt;.zip</code>。重跑会得到新的文件名。</p>
        </> : <p className="ku-empty">尚无本次结果。先扫描或复核，导出对象是那一次的结果包。</p>}
        {downloadNote && <p role="status" className="ku-success">{downloadNote}</p>}
      </section>}
    </div>

    <div className="ku-status">
      {undo && <p className="ku-undo" role="status">{undo.message}<button className="btn btn-ghost" type="button" onClick={() => { const run = undo.run; if (undoTimer.current) window.clearTimeout(undoTimer.current); setUndo(null); run(); }}>撤销</button></p>}
      {blocked && <p className="ku-blocked" role="status">扫描已禁用：还有 {unready.length} 项未就绪，例如 {unready[0]}。到「选来源与版本」补齐后再运行。</p>}
      {phase && <p className="ku-processing" role="status">{phase === 'export' ? '正在按本次输入重新计算并导出 ZIP…' : phase === 'scan' ? '正在扫描来源与条目…' : '正在复核全部条目（含未修订项）…'}</p>}
      {stale && <p className="ku-stale" role="status">已过期：输入已改变或已重新提交，旧结果不再展示。请重新扫描或复核后下载。</p>}
      {failure && <Failure error={failure}/>}
    </div>
    {/* 吸底范围只含按钮：状态说明留在容器外，否则那条禁扫提示会一起吸上来压住上方的输入框 */}
    <section className="ku-runbar" aria-label="本次运行">
      <div className="ku-runbar-actions">
        <button className="btn btn-primary" type="button" disabled={!!phase || blocked} aria-busy={phase === 'scan'} title={blocked ? unready[0] : undefined} onClick={() => { void execute('scan'); }}>{phase === 'scan' ? '正在扫描…' : '扫描 scan'}</button>
        <button className="btn btn-secondary" type="button" disabled={!!phase || blocked} aria-busy={phase === 'recheck'} title={blocked ? unready[0] : undefined} onClick={() => { void execute('recheck'); }}>{phase === 'recheck' ? '正在复核…' : '复核 recheck'}</button>
        <button className="btn btn-secondary" type="button" disabled={!!phase || !current} onClick={() => { void download(); }}>{phase === 'export' ? '正在导出…' : '下载 ZIP'}</button>
        <span className="ku-pager">
          <button className="btn btn-ghost" type="button" disabled={position === 0} onClick={() => go(KU_STEPS[position - 1])}>上一步</button>
          <button className="btn btn-ghost" type="button" disabled={position === KU_STEPS.length - 1} onClick={() => go(KU_STEPS[position + 1])}>下一步 · {KU_STEP_LABEL[KU_STEPS[position + 1] ?? 'export']}</button>
        </span>
      </div>
    </section>
  </div>;
}
