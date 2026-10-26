import { useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, DragEvent } from 'react';
import { Icon } from '../components/Icon';
import type { IconName } from '../components/Icon';
import { READ_ONLY, STATIC_SITE } from '../config';
import {
  EMPTY_REVIEW, PARENT_CONTEXT_FILE_KEYS, PARENT_CONTEXT_LIMIT_BYTES, PARENT_CONTEXT_MAX_BYTES, PUBLIC_DEMO_MESSAGE, ParentContextRequestError,
  buildReviewRecord, diagnosticMessage, formatBytes, parentContextFilesProblem, prepareParentContext, readFileBytes, reviewProblem,
} from '../api/parentContext';
import type { ParentContextFileKey, ParentContextFiles, ParentContextResult, ParentContextReview } from '../api/parentContext';
import './gate-audit.css';
import './parent-context.css';

const STEPS = ['导入', '准备', '预览', '复核', '下载'] as const;
const SLOTS: { key: ParentContextFileKey; file: string; label: string; hint: string; accept: string; icon: IconName }[] = [
  { key: 'source', file: 'source.txt', label: '完整父条原文', hint: '每个非空行对应一段；条首写“第X条”，完整条之间空一行。', accept: '.txt,text/plain', icon: 'file' },
  { key: 'segments', file: 'segments.json', label: '完整段快照', hint: '全部段的一页导出，position 从 1 连续覆盖原文非空行。', accept: '.json,application/json', icon: 'layers' },
  { key: 'retrieval', file: 'retrieval.json', label: '单次检索命中', hint: 'CitationResourcesV1，仅一条记录，保留真实标识与命中段。', accept: '.json,application/json', icon: 'trace' },
  { key: 'binding', file: 'binding.json', label: '绑定声明', hint: '登记原文与段快照的哈希及父条边界，可用 scripts/parent_context.py bind 生成。', accept: '.json,application/json', icon: 'link' },
];

type LoadedFile = { name: string; size: number; bytes: Uint8Array<ArrayBuffer> };
type Slot = { file: LoadedFile | null; reading: boolean; error: string | null };
type Slots = Record<ParentContextFileKey, Slot>;
const EMPTY_SLOT: Slot = { file: null, reading: false, error: null };
const emptySlots = (): Slots => ({ source: EMPTY_SLOT, segments: EMPTY_SLOT, retrieval: EMPTY_SLOT, binding: EMPTY_SLOT });

function download(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function FileSlot({ config, slot, disabled, onFile, onRemove }: {
  config: typeof SLOTS[number];
  slot: Slot;
  disabled: boolean;
  onFile: (file: File | undefined, count: number) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const [dragging, setDragging] = useState(false);
  const state = slot.reading ? 'reading' : slot.error ? 'error' : slot.file ? 'ready' : 'empty';
  // Dropping a file anywhere else would make the browser open it, so the slot always takes the drop, even when locked.
  const over = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = disabled ? 'none' : 'copy';
    if (!disabled) setDragging(true);
  };
  const drop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!disabled) onFile(event.dataTransfer?.files[0], event.dataTransfer?.files.length ?? 0);
  };
  return <div className="pc-slot" data-state={state} data-loaded={slot.file ? '' : undefined} data-dragging={dragging ? '' : undefined}>
    <input id={id} className="sr-only" type="file" accept={config.accept} aria-label={`${config.label} ${config.file}`} aria-describedby={`${id}-hint ${id}-status`} disabled={disabled}
      onChange={(event) => { const files = event.currentTarget.files; onFile(files?.[0], files?.length ?? 0); event.currentTarget.value = ''; }}/>
    <label htmlFor={id} className="pc-drop" onDragOver={over} onDragLeave={() => setDragging(false)} onDrop={drop}>
      <span className="pc-slot-icon" aria-hidden="true"><Icon name={slot.file && !slot.error ? 'check' : slot.error ? 'alert' : config.icon} size={16}/></span>
      <span className="pc-slot-body">
        <span className="pc-slot-title"><strong>{config.label}</strong><code>{config.file}</code></span>
        <small id={`${id}-hint`}>{config.hint}</small>
        <span id={`${id}-status`} className="pc-slot-status" aria-live="polite">
          {slot.reading && <span>正在读取…</span>}
          {!slot.reading && slot.file && <span className="pc-slot-file">{`${slot.file.name} · ${formatBytes(slot.file.size)}`}</span>}
          {slot.error && <span className="pc-slot-error">{slot.error}</span>}
          {!slot.reading && !slot.file && !slot.error && <span>点击选择，或把文件拖到这里</span>}
        </span>
      </span>
    </label>
    {slot.file && <button type="button" className="btn btn-ghost pc-remove" disabled={disabled} aria-label={`移除${config.label}`} onClick={onRemove}>移除</button>}
  </div>;
}

export function ParentContextView() {
  // Public builds never accept files: no reading, no request.
  const locked = READ_ONLY || STATIC_SITE;
  const id = useId();
  const [slots, setSlots] = useState<Slots>(emptySlots);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ParentContextResult | null>(null);
  const [failure, setFailure] = useState<{ message: string; code: string } | null>(null);
  const [review, setReview] = useState<ParentContextReview>(EMPTY_REVIEW);
  const [exported, setExported] = useState(false);
  const [notice, setNotice] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const mounted = useRef(true);
  const inflight = useRef(false);
  const request = useRef<AbortController | null>(null);
  const reads = useRef<Record<ParentContextFileKey, number>>({ source: 0, segments: 0, retrieval: 0, binding: 0 });

  // Leaving the page cancels the request; late answers and file reads never write state afterwards.
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; request.current?.abort(); };
  }, []);

  const sizes: Partial<Record<ParentContextFileKey, number>> = {};
  for (const key of PARENT_CONTEXT_FILE_KEYS) if (slots[key].file) sizes[key] = slots[key].file!.size;
  const loaded = Object.keys(sizes).length;
  const total = Object.values(sizes).reduce((sum, size) => sum + (size ?? 0), 0);
  const reading = PARENT_CONTEXT_FILE_KEYS.some((key) => slots[key].reading);
  const filesProblem = parentContextFilesProblem(sizes);
  const ready = !locked && !busy && !reading && filesProblem === null;
  const prepared = result?.status === 'PREPARED_FOR_REVIEW';
  const reviewIssue = result ? reviewProblem(result.status, review) : null;
  const reviewStarted = Boolean(review.identity.trim() || review.decision || review.note.trim() || review.checked);
  const downloadsPaused = reading || busy;
  const contextAllowed = !downloadsPaused && prepared && reviewIssue === null && review.decision === 'usable_for_downstream';
  const step = exported ? STEPS.length : result ? (reviewIssue === null ? 4 : reviewStarted ? 3 : 2) : filesProblem === null ? 1 : 0;

  /** Any change to the four inputs retires the previous result and review; nothing carries over to other files. */
  const invalidate = () => {
    setResult(null);
    setFailure(null);
    setReview(EMPTY_REVIEW);
    setExported(false);
    setNotice('');
    setAnnouncement('');
  };

  const setSlot = (key: ParentContextFileKey, update: (slot: Slot) => Slot) => setSlots((current) => ({ ...current, [key]: update(current[key]) }));

  const receive = async (key: ParentContextFileKey, file: File | undefined, count: number) => {
    if (locked || busy || !file) return;
    const token = ++reads.current[key];
    const rejected = count > 1 ? '一次只能放入一份文件，未替换。'
      : !file.size ? '所选文件为空，未替换。请选择包含内容的文件。'
        : file.size > PARENT_CONTEXT_MAX_BYTES ? '文件超过 8 MiB，未替换。请减小后重新选择。' : null;
    // A rejected file keeps the one already chosen, so the current inputs and result stay valid.
    if (rejected) { setSlot(key, (slot) => ({ ...slot, reading: false, error: rejected })); return; }
    setSlot(key, (slot) => ({ ...slot, reading: true, error: null }));
    let bytes: Uint8Array<ArrayBuffer>;
    try {
      bytes = await readFileBytes(file);
    } catch {
      if (mounted.current && token === reads.current[key]) setSlot(key, (slot) => ({ ...slot, reading: false, error: '文件读取失败，未替换。请重新选择。' }));
      return;
    }
    if (!mounted.current || token !== reads.current[key]) return;
    if (!bytes.length || bytes.length > PARENT_CONTEXT_MAX_BYTES) {
      setSlot(key, (slot) => ({ ...slot, reading: false, error: '文件读取后为空或超过 8 MiB，未替换。' }));
      return;
    }
    setSlot(key, () => ({ file: { name: file.name, size: bytes.length, bytes }, reading: false, error: null }));
    invalidate();
  };

  const remove = (key: ParentContextFileKey) => {
    if (busy) return;
    reads.current[key] += 1;
    setSlot(key, () => EMPTY_SLOT);
    invalidate();
  };

  const prepare = async () => {
    if (!ready || inflight.current) return;
    const files = Object.fromEntries(PARENT_CONTEXT_FILE_KEYS.map((key) => [key, slots[key].file!.bytes])) as ParentContextFiles;
    inflight.current = true;
    const controller = new AbortController();
    request.current = controller;
    invalidate();
    setBusy(true);
    try {
      const next = await prepareParentContext(files, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      setResult(next);
      setAnnouncement(next.status === 'PREPARED_FOR_REVIEW' ? '结果已返回：资料已准备，等待复核。' : '结果已返回：待复核，没有生成资料。');
    } catch (error) {
      if (!mounted.current || controller.signal.aborted) return;
      setFailure(error instanceof ParentContextRequestError ? { message: error.message, code: error.code } : { message: '父条资料准备未完成，请重试。', code: 'UNEXPECTED' });
    } finally {
      inflight.current = false;
      if (request.current === controller) request.current = null;
      if (mounted.current) setBusy(false);
    }
  };

  const updateReview = (patch: Partial<ParentContextReview>) => {
    setReview((current) => ({ ...current, ...patch }));
    setExported(false);
    setNotice('');
  };

  const downloadContext = () => {
    if (!result?.context || !contextAllowed) return;
    // Exactly the returned text: no BOM and no final newline, so the file keeps the reported SHA-256.
    download(result.context.text, 'context.txt', 'text/plain;charset=utf-8');
    setNotice('已在本机保存 context.txt。');
  };
  const downloadReport = () => {
    if (!result || downloadsPaused) return;
    download(`${JSON.stringify(result.report, null, 2)}\n`, 'report.json', 'application/json;charset=utf-8');
    setNotice('已在本机保存 report.json。');
  };
  const downloadReview = () => {
    if (!result || downloadsPaused) return;
    let record: ReturnType<typeof buildReviewRecord>;
    try {
      record = buildReviewRecord(result, review, new Date().toISOString());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '复核信息尚未完整。');
      return;
    }
    download(`${JSON.stringify(record, null, 2)}\n`, 'parent-context-review.json', 'application/json;charset=utf-8');
    setExported(true);
    setNotice('已在本机保存 parent-context-review.json，复核表没有上传。');
  };

  const hint = locked ? PUBLIC_DEMO_MESSAGE
    : busy ? '正在由本机后端准备，期间不能更换文件。'
      : reading ? '正在读取文件…'
        : filesProblem ?? `四份文件已就绪。资料上限 ${PARENT_CONTEXT_LIMIT_BYTES} UTF-8 字节，超过时整包待复核，不会截断。`;
  const facts = result?.facts;
  const context = result?.context;

  return <div className="audit-view pc-view">
    <section className="audit-panel pc-panel" aria-labelledby={`${id}-title`}>
      <div className="audit-panel-head">
        <div><h2 id={`${id}-title`}>导入四份文件，把命中段补全为完整父条</h2><p>只在本机准备资料：不调用模型、不核验回答、不改变质量门禁。准备成功只表示等待你复核，不代表通过质量门禁。</p></div>
        <span className="audit-chip pc-chip">本地处理 · 不调用模型</span>
      </div>
      {locked && <p className="readonly-notice"><Icon name="alert" size={16}/><span><strong>只读演示</strong>{PUBLIC_DEMO_MESSAGE}</span></p>}

      <ol className="pc-steps" aria-label="准备进度" style={{ '--pc-progress': Math.min(step, STEPS.length - 1) / (STEPS.length - 1) } as CSSProperties}>
        {STEPS.map((label, index) => <li key={label} className={index < step ? 'is-done' : index === step ? 'is-current' : undefined} aria-current={index === step ? 'step' : undefined}>
          <span className="pc-step-dot" aria-hidden="true">{index < step ? <Icon name="check" size={12}/> : index + 1}</span>
          <span>{label}{index < step && <span className="sr-only">（已完成）</span>}</span>
        </li>)}
      </ol>

      <div className="pc-section-head">
        <h3>四份文件</h3>
        <p className={`pc-total${total > PARENT_CONTEXT_MAX_BYTES ? ' is-over' : ''}`}>
          <span>{`已导入 ${loaded}/4 · 合计 `}<strong>{formatBytes(total)}</strong> / 8 MiB</span>
          <span className="pc-meter" aria-hidden="true"><span style={{ '--pc-fill': Math.min(1, total / PARENT_CONTEXT_MAX_BYTES) } as CSSProperties}/></span>
        </p>
      </div>
      <fieldset className="pc-files" disabled={locked || busy}>
        <legend className="sr-only">四份文件</legend>
        {SLOTS.map((config) => <FileSlot key={config.key} config={config} slot={slots[config.key]} disabled={locked || busy}
          onFile={(file, count) => { void receive(config.key, file, count); }} onRemove={() => remove(config.key)}/>)}
      </fieldset>

      <div className="pc-prepare-row">
        <button type="button" className="btn btn-primary pc-prepare" data-ready={ready ? '' : undefined} aria-busy={busy || undefined} disabled={!ready}
          aria-describedby={`${id}-prepare-hint`} onClick={() => { void prepare(); }}>
          <Icon name={busy ? 'refresh' : 'play'} size={15}/>{busy ? '正在准备…' : '准备父条资料'}
        </button>
        <p id={`${id}-prepare-hint`} className="pc-hint">{hint}</p>
      </div>
      {busy && <div className="pc-progress" role="status"><span className="pc-shimmer">正在准备父条资料，请保持页面打开…</span><span className="pc-progress-bar" aria-hidden="true"/></div>}
      {failure && <div className="error-notice pc-failure" role="alert"><Icon name="alert"/><div>
        <strong>父条资料未准备</strong><p>{failure.message}</p><p>四份文件仍保留，修正后可以再次准备。</p>
        <details className="pc-code"><summary>错误详情</summary><code>{failure.code}</code></details>
      </div></div>}
      <p className="sr-only" aria-live="polite">{announcement}</p>

      {result && <section className="pc-result" aria-labelledby={`${id}-result`} data-status={result.status}>
        <div className="pc-section-head">
          <h3 id={`${id}-result`}>{prepared ? '资料已准备，等待复核' : '待复核：没有生成资料'}</h3>
          <span className={`audit-chip ${prepared ? 'pc-chip' : 'audit-chip--warning'}`}>{result.status}</span>
        </div>
        {prepared && context && facts ? <>
          <p className="pc-lead">{`命中 ${facts.retrieved} 段，按原文补全为 ${facts.expanded} 段。`}这只说明四份文件彼此一致、命中段已补全为完整父条；不证明原文在法律全文中完整，也不代表通过质量门禁。</p>
          <dl className="audit-facts pc-facts">
            <div><dt>命中段</dt><dd>{facts.retrieved}<small>段</small></dd></div>
            <div><dt>补全后</dt><dd>{facts.expanded}<small>段</small></dd></div>
            <div><dt>原文共</dt><dd>{facts.sourceSegments}<small>段</small></dd></div>
            <div><dt>资料大小</dt><dd>{context.bytes}<small>{`/ ${PARENT_CONTEXT_LIMIT_BYTES} 字节`}</small></dd></div>
          </dl>
          <dl className="pc-ids">
            {([['文档', facts.documentId], ['数据集', facts.datasetId], ['消息', facts.messageId], ['会话', facts.conversationId]] as const).map(([label, value]) => value
              && <div key={label}><dt>{`${label} ID`}</dt><dd><code>{value}</code></dd></div>)}
            <div className="pc-hash"><dt>资料 SHA-256</dt><dd><code>{context.sha256}</code>
              <small className={result.hashCheck === 'matched' ? 'is-matched' : undefined}>{result.hashCheck === 'matched' ? '已在本机复算：四份文件与资料的哈希都和报告一致。' : '此浏览器无法计算哈希，没有在本机复算。'}</small></dd></div>
          </dl>
          {result.normalization && (result.normalization.bomIgnored || result.normalization.crlfMapped)
            && <p className="audit-note">{`原文${result.normalization.bomIgnored ? '带 UTF-8 BOM' : ''}${result.normalization.bomIgnored && result.normalization.crlfMapped ? '、' : ''}${result.normalization.crlfMapped ? '使用 CRLF 换行' : ''}：只在逐行对应时忽略，提交的原始字节和哈希保持不变。`}</p>}
          <div className="pc-preview-head">
            <h4>父条预览与原文位置</h4>
            <p className="audit-note">左列是原文位置，即第几个非空行。“命中”是检索返回的段，其余是按原文补全的同条段落。</p>
          </div>
          <ol className="pc-preview" aria-label="父条资料预览" tabIndex={0}>
            {context.lines.map((line, index) => <li key={`${line.segmentId}-${index}`} className={line.hit ? 'is-hit' : undefined} style={{ '--pc-row': Math.min(index, 12) } as CSSProperties}>
              <span className="pc-pos"><span className="sr-only">原文位置 </span>{line.position}</span>
              <span className="pc-line">{line.text}</span>
              <span className="pc-tag">{line.hit ? '命中' : line.metadata ? '标题/来源' : '补全'}</span>
            </li>)}
          </ol>
        </> : <>
          <p className="pc-lead">诊断没有通过，未输出任何部分资料。按下面的提示补正输入后，再重新准备。</p>
          <ul className="pc-diagnostics">{result.diagnostics.map((code, index) => {
            const message = diagnosticMessage(code);
            return <li key={`${code}-${index}`}><Icon name="alert" size={15}/><div><strong>{message.problem}</strong><p>{message.fix}</p><small>诊断码 <code>{code}</code></small></div></li>;
          })}</ul>
        </>}
        <details className="pc-limits"><summary>{`报告列出的已知边界（${result.limitations.length} 条）`}</summary><ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
      </section>}

      {result && <section className="pc-review" aria-labelledby={`${id}-review`}>
        <h3 id={`${id}-review`}>人工复核</h3>
        <p className="audit-note">复核人身份由你自填，记录不代表独立真人核验，也不产生门禁结论或人工标签。</p>
        <div className="pc-review-grid">
          <label className="pc-field"><span>复核人姓名或标识</span>
            <input type="text" value={review.identity} maxLength={200} autoComplete="off" onChange={(event) => updateReview({ identity: event.target.value })}/></label>
          <div className="pc-field" role="radiogroup" aria-labelledby={`${id}-decision`}>
            <span id={`${id}-decision`}>复核意见</span>
            <div className="pc-decisions">
              <div className="pc-decision">
                <label><input type="radio" name={`${id}-decision`} checked={review.decision === 'usable_for_downstream'} disabled={!prepared} aria-describedby={`${id}-usable`}
                  onChange={() => updateReview({ decision: 'usable_for_downstream' })}/><span>可用于后续处理</span></label>
                <small id={`${id}-usable`}>{prepared ? '已核对，资料可以交给后续步骤。' : '待复核结果不能选择此项。'}</small>
              </div>
              <div className="pc-decision">
                <label><input type="radio" name={`${id}-decision`} checked={review.decision === 'needs_further_check'} aria-describedby={`${id}-check`}
                  onChange={() => updateReview({ decision: 'needs_further_check' })}/><span>仍需核查</span></label>
                <small id={`${id}-check`}>需写明原因；资料不交给后续步骤。</small>
              </div>
            </div>
          </div>
          <label className="pc-field pc-field--wide"><span>{`原因或说明${review.decision === 'needs_further_check' ? '（必填）' : '（可选）'}`}</span>
            <textarea rows={3} maxLength={4000} value={review.note} onChange={(event) => updateReview({ note: event.target.value })}/></label>
          <label className="pc-check"><input type="checkbox" checked={review.checked} onChange={(event) => updateReview({ checked: event.target.checked })}/><span>已核对来源、父条范围和预览</span></label>
        </div>
        <p className={`pc-review-state${reviewIssue ? '' : ' is-complete'}`} aria-live="polite">{reviewIssue ?? '复核信息已完整，可以下载复核记录。'}</p>
      </section>}

      {result && <section className="pc-downloads" aria-labelledby={`${id}-downloads`}>
        <h3 id={`${id}-downloads`}>本地下载</h3>
        <div className="pc-download-actions">
          {prepared && <button type="button" className="btn btn-primary" disabled={!contextAllowed} onClick={downloadContext}><Icon name="file" size={15}/>下载资料 context.txt</button>}
          <button type="button" className="btn btn-secondary" disabled={downloadsPaused || reviewIssue !== null} onClick={downloadReview}><Icon name="pen" size={15}/>下载复核记录</button>
          <button type="button" className="btn btn-secondary" disabled={downloadsPaused} onClick={downloadReport}><Icon name="copy" size={15}/>下载报告 report.json</button>
        </div>
        <p className="audit-note">{downloadsPaused ? '正在读取新文件，下载暂不可用。' : prepared
          ? contextAllowed ? '资料和复核记录只保存在本机，复核表不会上传。' : '完成复核并选择“可用于后续处理”后，才能下载资料。'
          : '待复核结果没有可用资料，不提供 context.txt；可以下载报告和“仍需核查”的复核记录。'}</p>
        <p className="pc-download-notice" role="status">{notice}</p>
      </section>}
    </section>
  </div>;
}
