import { createRef, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { Icon } from '../components/Icon';
import { BeamConnector } from './landing/BeamConnector';
import { READ_ONLY, STATIC_SITE } from '../config';
import { formatBytes } from '../api/parentContext';
import { AXES, AXIS_LABELS, CHECK_LABELS, CV_HASH_COMMAND, CV_HASH_NOTE, CV_MAX_BYTES, CV_MAX_CITATIONS, CV_MAX_FILES, CV_PUBLIC_MESSAGE, KNOWN_BY_NOTE, STATE_LABELS, SUBCHECKS, checkCitationVersions, downloadCitationReport, inspectCitationFiles, loadCitationFile, makeCVInput, nextAction, subcheckOverview, suggestedViewUri } from '../api/citationVersion';
import type { CVBundle, CVCitation, CVInspection, CVRow, CVState, Evidence, InspectedFile, LocalCVFile } from '../api/citationVersion';
import './gate-audit.css';
import './citation-version.css';

type Draft = { id: string; label: string; fileId: string; provisionIndex: string; snapshotDate: string; checkDate: string; sameDate: boolean; extent: string; language: string; viewUri: string };
type Phase = 'reading' | 'inspecting' | 'checking' | null;
type Problem = { id: string; field: keyof Draft; message: string };
const newDraft = (id: string): Draft => ({ id, label: '', fileId: '', provisionIndex: '', snapshotDate: '', checkDate: '', sameDate: false, extent: '', language: '', viewUri: '' });
const show = (value: string | null | undefined) => value === null || value === undefined || value === '' ? '未提供 / 有歧义' : value;

/** Reuses the MIT Animated Beam adaptation for actual processing state, never a timed demo. */
function WorkflowStatus({ phase, fileCount, observed, hasReport, downloaded }: { phase: Phase; fileCount: number; observed: boolean; hasReport: boolean; downloaded: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const nodes = useMemo(() => Array.from({ length: 4 }, () => createRef<HTMLSpanElement>()), []);
  const active = hasReport ? 3 : phase === 'checking' ? 2 : observed ? 1 : 0;
  const steps = [
    { label: '导入原件', detail: phase === 'reading' ? '读取原始字节' : phase === 'inspecting' ? '解析原件字段' : fileCount ? `${fileCount} 份已导入` : '选择 CLML XML', icon: 'upload' },
    { label: '填写需求', detail: observed ? '选择条款与版本' : '等待读取原件', icon: 'sliders' },
    { label: '核对字段', detail: phase === 'checking' ? '正在检查' : hasReport ? '结果已返回' : '等待提交检查', icon: 'trace' },
    { label: '保存报告', detail: hasReport ? downloaded ? '已生成下载' : '可下载复核包' : '等待本次结果', icon: 'file' },
  ] as const;
  return <div className="cv-flow" ref={container} data-processing={phase || undefined}>
    {nodes.slice(1).map((_, index) => <BeamConnector key={`${index}-${phase ?? 'idle'}-${hasReport}`} containerRef={container} fromRef={nodes[index]} toRef={nodes[index + 1]} delay={0} animated={index === 0 ? phase === 'reading' || phase === 'inspecting' : index === 1 ? phase === 'checking' : hasReport}/>)}
    <ol className="cv-flow-steps" aria-label="操作流程">{steps.map((step, index) => <li key={step.label} className={index === active ? 'is-current' : index < active ? 'is-complete' : ''} aria-current={index === active ? 'step' : undefined}>
      <span className="cv-flow-node"><span className="cv-flow-anchor" ref={nodes[index]}/><Icon name={step.icon} size={18}/></span><span className="cv-flow-copy"><strong>{step.label}</strong><small>{step.detail}</small></span>
    </li>)}</ol>
  </div>;
}

function StateBadge({ state }: { state: CVState }) {
  return <span className={`cv-state cv-state--${state.toLowerCase()}`}>{STATE_LABELS[state]}<span className="cv-state-code">{state}</span></span>;
}
function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  return evidence.length ? <dl className="cv-evidence">{evidence.map((item, index) => <div key={index}><dt><code>{item.path}</code></dt><dd><code>{item.raw === null ? '未提供' : item.raw}</code></dd></div>)}</dl> : <p className="cv-note">没有可用字段证据。</p>;
}

function SourceObservation({ file, index, onRemove, disabled }: { file: InspectedFile; index: number; onRemove: () => void; disabled: boolean }) {
  const observed = file.observed;
  return <li className="cv-source">
    <div className="cv-source-head"><Icon name="file" size={18}/><div><strong>{file.name}</strong><p className="cv-note">{`文件 ${index + 1} · ${file.id} · ${file.size_bytes === null ? '大小未知' : formatBytes(file.size_bytes)}`}</p></div><button className="btn btn-ghost" type="button" disabled={disabled} aria-label={`移除文件 ${index + 1} ${file.name}`} onClick={onRemove}>移除</button></div>
    {file.status === 'INVALID_INPUT' && <p className="cv-error">{file.error || '文件无法解析，请更换符合要求的 CLML XML。'}</p>}
    {observed && <p className="cv-source-title">{show(observed.title)}<span className="cv-note">{`观察到 ${observed.provisions.length} 个条款记录`}</span></p>}
    <details className="cv-details"><summary>查看原件观察与文件绑定</summary><p className="cv-note">下面均来自所选原件；不会填入你的期望日期、地域或语言。相同文件名也按独立 ID 与字节哈希绑定。</p>
      <dl className="cv-identity"><div><dt>原始字节 SHA-256</dt><dd><code>{show(file.sha256)}</code></dd></div>{observed && <><div><dt>原件文档标识</dt><dd><code>{show(observed.document_id)}</code></dd></div><div><dt>原件语言</dt><dd><code>{show(observed.language)}</code></dd></div></>}</dl>
      <p>{CV_HASH_NOTE}</p><p><code>{CV_HASH_COMMAND}</code></p><p className="cv-note">在 PowerShell 中将 your-source.xml 换成原始 XML 文件路径后运行。</p>
      {observed?.ambiguities.length ? <div className="cv-warning"><strong>原件存在歧义</strong><ul>{observed.ambiguities.map((item, i) => <li key={i}>{item}</li>)}</ul></div> : null}
      {observed && observed.provisions.length > 0 && <div className="cv-table-scroll" tabIndex={0} role="region" aria-label={`${file.name} 原件条款字段`}><table><thead><tr><th>条款 / 视图 URI</th><th>视图日期</th><th>地域</th><th>开始 / 结束</th></tr></thead><tbody>{observed.provisions.map((part, i) => <tr key={i}><th><code>{show(part.provision_id)}</code><small><code>{show(part.view_uri)}</code></small></th><td>{show(part.snapshot_date)}</td><td>{show(part.extent)}</td><td><span>{show(part.start)}</span><br/><span>{show(part.end)}</span></td></tr>)}</tbody></table></div>}
    </details>
  </li>;
}

function CitationDraft({ draft, index, files, disabled, problem, onChange, onRemove }: { draft: Draft; index: number; files: InspectedFile[]; disabled: boolean; problem: Problem | null; onChange: (patch: Partial<Draft>) => void; onRemove: () => void }) {
  const [identifiersOpen, setIdentifiersOpen] = useState(false);
  const source = files.find((file) => file.id === draft.fileId);
  const provision = draft.provisionIndex === '' ? undefined : source?.observed?.provisions[Number(draft.provisionIndex)];
  const ownProblem = problem?.id === draft.id ? problem : null;
  const suggested = suggestedViewUri(provision?.view_uri, draft.snapshotDate);
  const names = (key: keyof Draft) => `${draft.id}-${key}`;
  useEffect(() => {
    if (ownProblem?.field === 'viewUri') setIdentifiersOpen(true);
  }, [ownProblem]);
  useEffect(() => {
    if (!ownProblem || (ownProblem.field === 'viewUri' && !identifiersOpen)) return;
    document.getElementById(`${draft.id}-${ownProblem.field}`)?.focus();
  }, [ownProblem, identifiersOpen, draft.id]);
  const ariaError = (field: keyof Draft) => ({ 'aria-invalid': ownProblem?.field === field || undefined, 'aria-describedby': ownProblem?.field === field ? `${draft.id}-problem` : undefined });
  return <fieldset className="cv-draft" disabled={disabled}>
    <legend>引用条目 {index + 1}</legend>
    <div className="cv-draft-head"><p className="cv-note">选择条款后，独立填写这次引用需要的版本条件。</p><button type="button" className="btn btn-ghost" aria-label={`删除引用条目 ${index + 1}`} onClick={onRemove}>删除条目</button></div>
    <div className="cv-fields">
      <label className="cv-field cv-field-wide" htmlFor={names('label')}><span>条目名称 <small>可选，不填写引文正文</small></span><input id={names('label')} name={names('label')} maxLength={200} value={draft.label} placeholder="例如：报告第二节的法规引用" onChange={(event) => onChange({ label: event.target.value })}/></label>
      <label className="cv-field" htmlFor={names('fileId')}><span>引用对应的文件</span><select id={names('fileId')} name={names('fileId')} value={draft.fileId} {...ariaError('fileId')} onChange={(event) => onChange({ fileId: event.target.value, provisionIndex: '', viewUri: '' })}><option value="">请选择文件</option>{files.map((file, i) => <option key={file.id} value={file.id}>{`文件 ${i + 1} · ${file.name} · ${file.id}${file.status === 'INVALID_INPUT' ? '（输入无效）' : ''}`}</option>)}</select></label>
      <label className="cv-field" htmlFor={names('provisionIndex')}><span>引用的条款</span><select id={names('provisionIndex')} name={names('provisionIndex')} value={draft.provisionIndex} disabled={disabled || !source?.observed?.provisions.length} {...ariaError('provisionIndex')} onChange={(event) => onChange({ provisionIndex: event.target.value, viewUri: '' })}><option value="">请明确选择条款</option>{source?.observed?.provisions.map((part, i) => <option key={i} value={String(i)}>{`${show(part.provision_id)} · 记录 ${i + 1}`}</option>)}</select></label>
      {source && <p className="cv-note cv-field-wide">文档和条款标识来自你选择的文件。请自行确认这份资料对应本次引用；字段相符不证明来源真实性。{source.status === 'INVALID_INPUT' && <strong className="cv-error"> 该文件无法解析，请更换后再选择条款。</strong>}</p>}
      <label className="cv-field" htmlFor={names('snapshotDate')}><span>所需视图日期</span><input type="date" aria-label="所需视图日期" id={names('snapshotDate')} name={names('snapshotDate')} value={draft.snapshotDate} {...ariaError('snapshotDate')} onChange={(event) => onChange({ snapshotDate: event.target.value, viewUri: '' })}/><small>你需要引用哪一天的视图。</small></label>
      <div className="cv-field"><label htmlFor={names('checkDate')}>片段查询日期</label><input type="date" id={names('checkDate')} name={names('checkDate')} disabled={disabled || draft.sameDate} value={draft.sameDate ? draft.snapshotDate : draft.checkDate} {...ariaError('checkDate')} onChange={(event) => onChange({ checkDate: event.target.value })}/><label className="cv-checkbox"><input type="checkbox" checked={draft.sameDate} onChange={(event) => onChange({ sameDate: event.target.checked })}/>与所需视图日期相同</label></div>
      <label className="cv-field" htmlFor={names('extent')}><span>所需地域</span><select id={names('extent')} name={names('extent')} value={draft.extent} {...ariaError('extent')} onChange={(event) => onChange({ extent: event.target.value })}><option value="">请选择地域</option><option value="E">英格兰（E）</option><option value="W">威尔士（W）</option><option value="S">苏格兰（S）</option><option value="N.I.">北爱尔兰（N.I.）</option></select></label>
      <label className="cv-field" htmlFor={names('language')}><span>所需语言</span><select id={names('language')} name={names('language')} value={draft.language} {...ariaError('language')} onChange={(event) => onChange({ language: event.target.value })}><option value="">请选择语言</option><option value="en">英语（en）</option><option value="cy">威尔士语（cy）</option></select></label>
    </div>
    <div className="cv-uri-action"><button type="button" className="btn btn-secondary" disabled={disabled || !suggested} onClick={() => { if (suggested) { onChange({ viewUri: suggested }); setIdentifiersOpen(true); } }}>按此日期准备核对</button><p className="cv-note">只用所选条款的路径与所需日期生成请求 URI，仍需点击“检查版本字段”。{draft.viewUri ? ' 请求 URI 已填写，可在下方查看。' : ' 也可展开下方手动填写。'}</p></div>
    <details className="cv-details" open={identifiersOpen} onToggle={(event) => setIdentifiersOpen(event.currentTarget.open)}><summary>查看 / 调整标识</summary><dl className="cv-identity"><div><dt>所选文件的文档标识</dt><dd><code>{show(source?.observed?.document_id)}</code></dd></div><div><dt>所选条款标识</dt><dd><code>{show(provision?.provision_id)}</code></dd></div><div><dt>原件中的视图 URI（观察）</dt><dd><code>{show(provision?.view_uri)}</code></dd></div></dl><label className="cv-field" htmlFor={names('viewUri')}><span>所需视图 URI（请求）</span><input type="text" aria-label="所需视图 URI（请求）" id={names('viewUri')} name={names('viewUri')} value={draft.viewUri} maxLength={2000} {...ariaError('viewUri')} onChange={(event) => onChange({ viewUri: event.target.value })}/><small>填写 legislation.gov.uk 的视图标识，结尾日期应为所需视图日期；不要填写 /data.xml 文件路径。</small></label></details>
    {ownProblem && <p id={`${draft.id}-problem`} className="cv-error" role="alert">{ownProblem.message}</p>}
  </fieldset>;
}

function ResultRow({ row, index, bundle }: { row: CVRow; index: number; bundle: CVBundle }) {
  const file = bundle.report.inputs.files.find((item) => item.id === row.file_id);
  return <article className="cv-result-row" aria-labelledby={`result-${row.id}`}>
    <div className="cv-section-head"><div><h3 id={`result-${row.id}`}>{row.label || `引用条目 ${index + 1}`}</h3><p className="cv-note">{`${file?.name ?? '未知文件'} · ${row.file_id} · ${row.id}`}</p></div><a className="btn btn-ghost" href={`#${row.id}-fileId`} onClick={(event) => { event.preventDefault(); document.getElementById(`${row.id}-fileId`)?.focus(); }}>调整本条需求<Icon name="arrow" size={14}/></a></div>
    <dl className="cv-axes">{AXES.map((key) => <div key={key}><dt>{AXIS_LABELS[key]}</dt><dd><StateBadge state={row.output.axes[key]}/></dd>{key === 'known_by' && <p>{KNOWN_BY_NOTE}</p>}</div>)}</dl>
    {row.output.input_errors.length > 0 && <div className="cv-warning"><strong>先修正输入</strong><ul>{row.output.input_errors.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}
    <div className="cv-checks">{SUBCHECKS.map((key) => {
      const part = row.output.subchecks[key];
      const overview = subcheckOverview(part);
      return <div key={key} className="cv-check" data-check={key} data-state={part.state}>
        <div className="cv-check-heading"><h4>{CHECK_LABELS[key]}</h4><StateBadge state={part.state}/></div>
        <p className="cv-check-reason"><span>{overview.reasonLabel}：</span>{overview.reason}</p>
        <p className="cv-note cv-check-count">{overview.counts}</p>
        <p className="cv-next"><strong>下一步</strong>{nextAction(key, part.state)}</p>
        <details className="cv-check-evidence" open={part.state === 'MISMATCH'}><summary>查看完整原字段与逐项原因</summary>
          <div className="cv-evidence-scroll" role="region" aria-label={`引用条目 ${index + 1} ${CHECK_LABELS[key]}完整依据`} tabIndex={0}>
            <div className="cv-check-body">{part.checks.map((item, i) => <div key={i}><p>{item.reason}<span className="cv-reason-state">{STATE_LABELS[item.state]}</span></p><EvidenceList evidence={item.evidence}/></div>)}{part.checks.length === 0 && <p className="cv-note">没有逐项依据；输入问题保留在上方。</p>}</div>
          </div>
        </details>
      </div>;
    })}</div>
    <details className="cv-details"><summary>查看本次请求、文件哈希及完整原始输出</summary><p className="cv-note">这份结果仅绑定下面的原始字节与请求。</p><p><code>{file?.sha256 ?? '未提供哈希'}</code></p><pre tabIndex={0} role="region" aria-label={`引用条目 ${index + 1} 完整原始输出`}>{JSON.stringify({ request: bundle.input.citations[index].request, output: row.output }, null, 2)}</pre></details>
  </article>;
}

export function CitationVersionView() {
  const locked = READ_ONLY || STATIC_SITE;
  const id = useId();
  const [files, setFiles] = useState<LocalCVFile[]>([]);
  const [inspection, setInspection] = useState<CVInspection | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([newDraft('citation-1')]);
  const [phase, setPhase] = useState<Phase>(null);
  const [failure, setFailure] = useState('');
  const [notice, setNotice] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [current, setCurrent] = useState<CVBundle | null>(null);
  const [previous, setPrevious] = useState<CVBundle | null>(null);
  const [dragging, setDragging] = useState(false);
  const [downloadNote, setDownloadNote] = useState('');
  const counter = useRef({ file: 0, citation: 1, task: 0 });
  const mounted = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const currentRef = useRef<CVBundle | null>(null);
  const total = files.reduce((sum, file) => sum + file.size_bytes, 0);
  const fileBusy = phase === 'reading' || phase === 'inspecting';
  const observedFiles = inspection?.files ?? [];

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; counter.current.task += 1; controller.current?.abort(); };
  }, []);

  const retire = (message = '') => {
    counter.current.task += 1;
    controller.current?.abort(); controller.current = null;
    setPhase(null); setFailure(''); setProblem(null); setDownloadNote('');
    if (currentRef.current) { setPrevious(currentRef.current); currentRef.current = null; setCurrent(null); }
    setNotice(message);
  };
  const changeDraft = (draftId: string, patch: Partial<Draft>) => {
    if (locked) return;
    retire('输入已改变，请重新检查。上次报告仅对应原来的输入。');
    setDrafts((items) => items.map((item) => item.id === draftId ? { ...item, ...patch } : item));
  };
  const inspect = async (nextFiles: LocalCVFile[]) => {
    const token = ++counter.current.task;
    const request = new AbortController(); controller.current = request;
    setPhase('inspecting'); setFailure(''); setNotice('');
    try {
      const result = await inspectCitationFiles(nextFiles, request.signal);
      if (!mounted.current || token !== counter.current.task) return;
      setInspection(result); setNotice('原件观察已就绪。请明确选择条款，并填写你的版本需求。');
    } catch (error) {
      if (mounted.current && token === counter.current.task) setFailure(error instanceof Error ? error.message : '原件观察未完成，请重试。');
    } finally { if (mounted.current && token === counter.current.task) { setPhase(null); controller.current = null; } }
  };
  const addFiles = async (incoming: File[]) => {
    if (locked || fileBusy || !incoming.length) return;
    const message = files.length + incoming.length > CV_MAX_FILES ? '最多导入 10 份 XML，请先移除部分文件。'
      : incoming.some((file) => !/\.xml$/i.test(file.name)) ? '请选择扩展名为 .xml 的 CLML 文件。'
        : total + incoming.reduce((sum, file) => sum + file.size, 0) > CV_MAX_BYTES ? '所有 XML 合计不能超过 5 MiB，请减小后重新选择。' : '';
    if (message) { setFailure(message); return; }
    retire(); setInspection(null); setPhase('reading');
    const token = ++counter.current.task;
    try {
      const additions: LocalCVFile[] = [];
      for (const file of incoming) {
        additions.push(await loadCitationFile(file, `file-${++counter.current.file}`));
        if (!mounted.current || token !== counter.current.task) return;
      }
      const next = [...files, ...additions];
      if (next.reduce((sum, file) => sum + file.size_bytes, 0) > CV_MAX_BYTES) throw new Error('读取后的文件合计超过 5 MiB，请减小后重试。');
      setFiles(next); await inspect(next);
    } catch (error) {
      if (mounted.current && token === counter.current.task) { setPhase(null); setFailure(error instanceof Error ? error.message : '文件读取失败，请重新选择。'); }
    }
  };
  const removeFile = (fileId: string) => {
    if (locked || fileBusy) return;
    retire('已移除文件，请核对条目后重新检查。');
    setFiles((items) => items.filter((file) => file.id !== fileId));
    setInspection((value) => value ? { ...value, files: value.files.filter((file) => file.id !== fileId) } : null);
    setDrafts((items) => items.map((item) => item.fileId === fileId ? { ...item, fileId: '', provisionIndex: '', viewUri: '' } : item));
  };
  const runCheck = async () => {
    if (locked || phase || !inspection || !files.length || !drafts.length) return;
    const citations: CVCitation[] = [];
    for (const draft of drafts) {
      const file = files.find((item) => item.id === draft.fileId);
      const source = observedFiles.find((item) => item.id === draft.fileId);
      const part = draft.provisionIndex === '' ? undefined : source?.observed?.provisions[Number(draft.provisionIndex)];
      const issue: [keyof Draft, string] | null = !file || !source?.observed?.document_id ? ['fileId', '请选择能解析出唯一文档标识的文件。']
        : !part?.provision_id ? ['provisionIndex', '请明确选择引用的条款。']
          : !draft.snapshotDate ? ['snapshotDate', '请填写所需视图日期；原件日期不会自动作为你的要求。']
            : !(draft.sameDate ? draft.snapshotDate : draft.checkDate) ? ['checkDate', '请填写片段查询日期，或明确选择与所需视图日期相同。']
              : !draft.extent ? ['extent', '请选择所需地域。'] : !draft.language ? ['language', '请选择所需语言。']
                : !draft.viewUri.trim() ? ['viewUri', '请点击“按此日期准备核对”，或手动填写所需视图 URI。'] : null;
      if (issue) { setProblem({ id: draft.id, field: issue[0], message: issue[1] }); return; }
      citations.push({ id: draft.id, label: draft.label, file_id: file!.id, request: { source_sha256: file!.sha256, document_id: source!.observed!.document_id!, provision_id: part!.provision_id!, view_uri: draft.viewUri.trim(), snapshot_date: draft.snapshotDate, check_date: draft.sameDate ? draft.snapshotDate : draft.checkDate, extent: draft.extent, language: draft.language } });
    }
    retire();
    const token = ++counter.current.task;
    const request = new AbortController(); controller.current = request; setPhase('checking');
    const input = makeCVInput(files, citations);
    try {
      const result = await checkCitationVersions(input, files, request.signal);
      if (!mounted.current || token !== counter.current.task) return;
      currentRef.current = result; setCurrent(result); setNotice(`已核对 ${result.report.counts.total} 条。请逐项查看冲突与待复核内容。`);
    } catch (error) {
      if (mounted.current && token === counter.current.task) setFailure(error instanceof Error ? error.message : '检查未完成，请重试。');
    } finally { if (mounted.current && token === counter.current.task) { setPhase(null); controller.current = null; } }
  };
  const onDrop = (event: DragEvent<HTMLLabelElement>) => { event.preventDefault(); setDragging(false); if (!locked && !fileBusy) void addFiles(Array.from(event.dataTransfer?.files ?? [])); };
  const download = (bundle: CVBundle, format: 'html' | 'json', historical = false) => {
    downloadCitationReport(bundle, format, historical);
    setDownloadNote(`已生成${historical ? '上次' : '本次'}${format === 'html' ? ' HTML 报告' : ' JSON 复核包'}下载。`);
  };

  return <div className="audit-view cv-view" data-phase={phase || undefined}>
    <WorkflowStatus phase={phase} fileCount={files.length} observed={Boolean(inspection)} hasReport={Boolean(current)} downloaded={Boolean(current && downloadNote && !downloadNote.includes('上次'))}/>
    <div className="cv-workbench" data-has-observation={Boolean(inspection)}>
    <section className="audit-panel cv-panel cv-materials" aria-labelledby={`${id}-files-title`}>
      <div className="audit-panel-head"><div><h2 id={`${id}-files-title`}>原始资料</h2><p>上传原件，保留可追溯的文件绑定。</p></div><span className="audit-chip cv-local">本地检查</span></div>
      {locked && <p className="readonly-notice" role="status"><Icon name="alert" size={16}/><span><strong>只读演示</strong>{CV_PUBLIC_MESSAGE}</span></p>}
      <div className="cv-upload" data-dragging={dragging || undefined}><input id={`${id}-upload`} className="sr-only" type="file" multiple accept=".xml,application/xml,text/xml" aria-label="添加 CLML XML 文件" aria-describedby={`${id}-upload-hint`} disabled={locked || fileBusy} onChange={(event) => { void addFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ''; }}/><label htmlFor={`${id}-upload`} onDragOver={(event) => { event.preventDefault(); if (!locked && !fileBusy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}><span className="cv-upload-icon"><Icon name="upload" size={25}/></span><span><strong>选择或拖入 XML 文件</strong><small id={`${id}-upload-hint`}>最多 10 份，合计不超过 5 MiB。<br/>同名文件按独立编号与哈希区分。</small></span><span className="cv-upload-cta" aria-hidden="true">浏览文件<Icon name="arrow" size={14}/></span></label></div>
      <div className="cv-file-budget"><p className="cv-note">{`${files.length} / 10 份 · ${formatBytes(total)} / 5 MiB`}</p><span>原始字节</span></div>
      {observedFiles.length > 0 ? <ul className="cv-sources">{observedFiles.map((file, index) => <SourceObservation key={file.id} file={file} index={index} disabled={locked || fileBusy} onRemove={() => removeFile(file.id)}/>)}</ul> : files.length > 0 && <ul className="cv-pending-files">{files.map((file) => <li key={file.id}><span>{`${file.name} · ${file.id} · ${formatBytes(file.size_bytes)}`}</span><button type="button" className="btn btn-ghost" disabled={locked || fileBusy} onClick={() => removeFile(file.id)} aria-label={`移除 ${file.id}`}>移除</button></li>)}</ul>}
      {!inspection && files.length > 0 && !phase && <button type="button" className="btn btn-secondary cv-fit" disabled={locked} onClick={() => { retire(); void inspect(files); }}>重新读取原件观察</button>}
      {phase && <div className="cv-running" role="status"><p>{phase === 'reading' ? '正在读取原始字节并计算哈希…' : phase === 'inspecting' ? '正在解析原件观察…' : '正在核对版本字段…'}</p><button type="button" className="btn btn-secondary" onClick={() => retire('本次操作已取消，已完成读取的文件和需求仍保留。')}>取消本次操作</button></div>}
      {failure && <div className="error-notice cv-failure" role="alert"><Icon name="alert"/><div><strong>本次操作未完成</strong><p>{failure}</p></div></div>}
      <p className="cv-local-note"><Icon name="shield" size={14}/><span>由你配置的本地后端处理，不调用模型。</span></p>
    </section>

    <section className="audit-panel cv-panel cv-requirements" aria-labelledby={`${id}-requirements-title`}>
      <div className="audit-panel-head"><div><h2 id={`${id}-requirements-title`}>你的引用需求</h2><p>{inspection ? '明确选择条款，再独立填写版本条件。' : '先导入 XML 并读取原件观察，再选择引用条款。'}</p></div>{inspection && <span className="cv-note cv-citation-count">{`${drafts.length} / ${CV_MAX_CITATIONS} 条`}</span>}</div>
      {!inspection ? <div className="cv-awaiting"><div className="cv-awaiting-title"><Icon name="layers" size={22}/><h3>从原件开始，按需求核对</h3></div><p>导入后，在这里定义本次引用。原件中的日期、地域和语言不会自动成为你的要求。</p><dl><div><dt>引用条款</dt><dd>从所选原件明确选择</dd></div><div><dt>日期条件</dt><dd>填写所需视图日与片段查询日</dd></div><div><dt>地域与语言</dt><dd>独立选择本次引用需要的版本</dd></div></dl><p className="cv-awaiting-foot">检查后逐项显示冲突、原字段和下一步；known_by 始终保留复核。</p></div> : <>
      {drafts.map((draft, index) => <CitationDraft key={draft.id} draft={draft} index={index} files={observedFiles} disabled={locked || fileBusy} problem={problem} onChange={(patch) => changeDraft(draft.id, patch)} onRemove={() => { retire('已删除条目，请重新检查。'); setDrafts((items) => items.filter((item) => item.id !== draft.id)); }}/>) }
      <div className="cv-checkbar"><button type="button" className="btn btn-secondary" disabled={locked || fileBusy || drafts.length >= CV_MAX_CITATIONS} onClick={() => { retire('已添加条目，请填写后重新检查。'); setDrafts((items) => [...items, newDraft(`citation-${++counter.current.citation}`)]); }}>添加引用条目</button><button type="button" className="btn btn-primary cv-run-check" disabled={locked || Boolean(phase) || !files.length || !drafts.length} onClick={() => { void runCheck(); }}><Icon name="check" size={15}/>{phase === 'checking' ? '正在检查…' : '检查版本字段'}<Icon name="arrow" size={14}/></button><p className="cv-note">修改任一输入，本次报告立即失效；重新检查后才可下载。</p></div>
      </>}
      <p className="cv-note cv-announcement" role="status">{notice}</p>
    </section>
    </div>

    {current && <section className="audit-panel cv-panel cv-results" aria-labelledby={`${id}-results-title`}>
      <div className="audit-panel-head"><div><h2 id={`${id}-results-title`}>本次字段核对</h2><p>逐条显示三条判断轴与七项字段检查。字段相符只说明本次字段核对，没有总通过结论。</p></div></div>
      <dl className="cv-counts">{([['全部条目', current.report.counts.total], ['有效输入', current.report.counts.valid_input], ['输入无效', current.report.counts.invalid], ['存在冲突', current.report.counts.conflicts], ['字段待复核', current.report.counts.field_review], ['known_by 待复核', current.report.counts.known_by_review]] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <p className="cv-note">有效输入 + 输入无效 = 全部条目。冲突、字段待复核、known_by 待复核按有效输入统计，可同时出现，不相加。</p>
      {current.report.rows.map((row, index) => <ResultRow key={row.id} row={row} index={index} bundle={current}/>)}
      <div className="cv-export"><h3>保存本次报告</h3><p className="cv-note">HTML 便于阅读。JSON 复核包含原始文件字节、完整请求与报告，可在离线复核脚本中重新执行比较。</p><div className="cv-actions"><button type="button" className="btn btn-secondary" onClick={() => download(current, 'html')}><Icon name="file" size={15}/>下载本次 HTML 报告</button><button type="button" className="btn btn-secondary" onClick={() => download(current, 'json')}><Icon name="file" size={15}/>下载本次 JSON 复核包</button></div></div>
      <details className="cv-details"><summary>检查范围、检查器与计数口径</summary><ul>{current.report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul><pre>{JSON.stringify({ checker: current.report.checker, count_semantics: current.report.count_semantics }, null, 2)}</pre></details>
    </section>}
    {previous && <section className="audit-panel cv-previous" aria-labelledby={`${id}-previous-title`}><div><h2 id={`${id}-previous-title`}>上次报告 · 历史输入</h2><p className="cv-note">{`保留 ${previous.report.counts.total} 条原请求及其文件绑定。仅供历史复核，不代表当前输入。`}</p></div><div className="cv-actions"><button type="button" className="btn btn-ghost" onClick={() => download(previous, 'html', true)}>下载上次 HTML</button><button type="button" className="btn btn-ghost" onClick={() => download(previous, 'json', true)}>下载上次 JSON</button></div></section>}
    <p className="cv-note" role="status">{downloadNote}</p>
  </div>;
}
