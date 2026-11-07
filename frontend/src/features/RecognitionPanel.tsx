import { useEffect, useRef, useState } from 'react';
import { BYOK, READ_ONLY, STATIC_SITE } from '../config';
import type { ImportedInput } from '../api/inputFiles';
import { fetchRecognition, fetchRecognitionMedia, fetchSourceLocations, recognizeInput, RECOGNITION_MESSAGES } from '../api/recognition';
import type { RecognizedDocument, RecognitionKind, RecognitionOutcome, SourceLocations, SourceRef } from '../api/recognition';
import { FileImportControl } from './FileImportControl';
import './recognition.css';

export interface RecognitionSelection { source: ImportedInput; recognitionPath: string; textPath: string; textBytes: number }
export interface SavedRecognitionRecord { id: string; docPath: string | null }
interface Props {
  source?: ImportedInput;
  recognitionPath?: string;
  documentPath: string;
  savedRecord?: SavedRecognitionRecord;
  disabled: boolean;
  readOnly: boolean;
  onSource: (input?: ImportedInput) => void;
  onRecognized: (selection: RecognitionSelection) => void;
  onBusy: (busy: boolean) => void;
}

const LOCATION_LABELS = {
  located: '已定位', ambiguous: '重复命中，请选择位置', not_found: '未在识别文字中找到引文',
  empty_quote: '原陈述没有引文', invalid_quote: '原引文格式不合法',
};
const time = (ms: number) => `${(ms / 1000).toFixed(2)} 秒`;
function refLabel(ref: SourceRef) {
  return ref.line_id ? `第 ${ref.page} 页 · ${ref.line_id}` : `${time(ref.start_ms!)}–${time(ref.end_ms!)}`;
}
function Confidence({ value, missing = false }: { value: number | null; missing?: boolean }) {
  return <span className={`recognition-confidence${value === null || missing || value < 0.8 ? ' needs-review' : ''}`}>
    {value === null ? '置信度未提供' : `置信度 ${value}${value < 0.8 ? ' · 低置信' : ''}${missing ? ' · 部分置信度未提供' : ''}`}
  </span>;
}

export function RecognitionPanel({ source, recognitionPath, documentPath, savedRecord, disabled, readOnly, onSource, onRecognized, onBusy }: Props) {
  const blocked = readOnly || READ_ONLY || STATIC_SITE || BYOK;
  const [kind, setKind] = useState<RecognitionKind>(source?.kind === 'audio' ? 'asr' : 'ocr');
  const [input, setInput] = useState<ImportedInput | undefined>(source);
  const inputRef = useRef(input);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<RecognitionOutcome | null>(null);
  const [document, setDocument] = useState<RecognizedDocument | null>(null);
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locations, setLocations] = useState<SourceLocations | null>(null);
  const [selected, setSelected] = useState<{ key: string; refs: SourceRef[] } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const operation = useRef<'recognize' | 'read' | 'locations' | null>(null);
  const loadedPath = useRef<string | null>(null);
  const blobUrl = useRef<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const metadataReady = useRef(false);
  const pendingSeek = useRef<number | null>(null);
  const callbacks = useRef({ onSource, onRecognized, onBusy });
  callbacks.current = { onSource, onRecognized, onBusy };
  const locationIdentity = `${savedRecord?.id ?? ''}|${savedRecord?.docPath ?? ''}|${documentPath}|${recognitionPath ?? ''}`;
  const latestLocationIdentity = useRef(locationIdentity);
  latestLocationIdentity.current = locationIdentity;

  const resetReview = () => {
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
    blobUrl.current = null; loadedPath.current = null;
    metadataReady.current = false; pendingSeek.current = null;
    setDocument(null); setMediaUrl(null); setLocations(null); setSelected(null); setOutcome(null); setError(null);
  };
  const begin = (type: typeof operation.current) => {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current; operation.current = type;
    setBusy(true); callbacks.current.onBusy(true); setError(null);
    return current;
  };
  const finish = (current: AbortController) => {
    if (controller.current !== current) return;
    controller.current = null; operation.current = null;
    if (!current.signal.aborted) { setBusy(false); callbacks.current.onBusy(false); }
  };
  const reportError = (cause: unknown, current: AbortController) => {
    if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : '识别或原件回看未完成，请检查本地服务。');
  };
  const readDocument = async (path: string, original: ImportedInput | undefined, current: AbortController) => {
    const result = await fetchRecognition(path, current.signal);
    if (current.signal.aborted) return;
    if (original && (result.sidecar.source.relative_path !== original.relative_path
      || (result.sidecar.recognition.kind === 'ocr' ? original.kind !== 'image' : original.kind !== 'audio'))) throw new Error('识别文字与当前原件不一致，结果未采用。');
    const media = await fetchRecognitionMedia(result.sidecar.source.relative_path, result.sidecar.source.sha256, current.signal);
    if (current.signal.aborted) return;
    const verifiedOriginal: ImportedInput = original ?? {
      kind: result.sidecar.recognition.kind === 'ocr' ? 'image' : 'audio',
      relative_path: result.sidecar.source.relative_path,
      file_name: result.sidecar.source.relative_path.split('/').pop()!, size_bytes: media.size,
    };
    const url = URL.createObjectURL(media);
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
    blobUrl.current = url; loadedPath.current = path;
    metadataReady.current = false; pendingSeek.current = null;
    setDocument(result); setMediaUrl(url); setSelected(null); setLocations(null);
    inputRef.current = verifiedOriginal; setInput(verifiedOriginal); setKind(result.sidecar.recognition.kind);
    callbacks.current.onRecognized({ source: verifiedOriginal, recognitionPath: path, textPath: result.sidecar.text.relative_path, textBytes: new TextEncoder().encode(result.text).length });
  };

  useEffect(() => () => {
    controller.current?.abort();
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
    callbacks.current.onBusy(false);
  }, []);

  useEffect(() => {
    if (blocked) return;
    if (source?.relative_path !== inputRef.current?.relative_path) {
      controller.current?.abort(); controller.current = null; operation.current = null;
      setBusy(false); callbacks.current.onBusy(false); resetReview();
      inputRef.current = source; setInput(source); setKind(source?.kind === 'audio' ? 'asr' : 'ocr');
    }
    if (!recognitionPath || !source || loadedPath.current === recognitionPath) return;
    resetReview();
    const current = begin('read');
    void readDocument(recognitionPath, source, current).catch((cause) => reportError(cause, current)).finally(() => finish(current));
    return () => { current.abort(); };
    // These values identify the original; callbacks are read through a ref so configuration updates do not restart a read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recognitionPath, source?.relative_path, blocked]);

  useEffect(() => {
    setLocations(null); setSelected(null);
    if (operation.current === 'locations' || (operation.current === 'read' && !source)) {
      controller.current?.abort(); controller.current = null; operation.current = null;
      setBusy(false); callbacks.current.onBusy(false);
    }
  }, [locationIdentity]);

  const acceptSource = (original?: ImportedInput) => {
    resetReview(); inputRef.current = original; setInput(original); callbacks.current.onSource(original);
  };
  const recognize = async () => {
    if (blocked || disabled || busy || controller.current || !input) return;
    resetReview();
    // A new recognition attempt withdraws the previous generated document until it is verified again.
    callbacks.current.onSource(input);
    const current = begin('recognize');
    try {
      const result = await recognizeInput(kind, input.relative_path, current.signal);
      if (current.signal.aborted) return;
      setOutcome(result);
      if (result.status === 'ok') await readDocument(result.recognition_path, input, current);
    } catch (cause) { reportError(cause, current); }
    finally { finish(current); }
  };

  const canRestore = !input && !document && savedRecord?.docPath === documentPath
    && /^[a-f0-9]{32}$/.test(savedRecord.id) && /^work\/uploads\/[a-f0-9]{32}\.txt$/.test(documentPath);
  const restore = async () => {
    if (blocked || disabled || busy || controller.current || !canRestore) return;
    const current = begin('read');
    try { await readDocument(documentPath.replace(/\.txt$/, '.recognition.json'), undefined, current); }
    catch (cause) { reportError(cause, current); }
    finally { finish(current); }
  };

  const selectRefs = (key: string, refs: SourceRef[]) => {
    setSelected({ key, refs });
    const timestamp = refs.find((ref) => ref.start_ms !== undefined)?.start_ms;
    if (timestamp !== undefined) {
      pendingSeek.current = timestamp / 1000;
      if (audio.current && metadataReady.current) {
        audio.current.currentTime = pendingSeek.current; pendingSeek.current = null;
      }
    }
  };
  const canLocate = Boolean(savedRecord && /^[a-f0-9]{32}$/.test(savedRecord.id)
    && savedRecord.docPath === documentPath && document?.sidecar.text.relative_path === documentPath && loadedPath.current);
  const locate = async (rebuild: boolean) => {
    if (blocked || disabled || busy || controller.current || !canLocate || !savedRecord || !loadedPath.current || !document) return;
    const identity = latestLocationIdentity.current;
    const current = begin('locations'); setLocations(null); setSelected(null);
    try {
      const result = await fetchSourceLocations(savedRecord.id, loadedPath.current, rebuild, current.signal);
      if (current.signal.aborted || identity !== latestLocationIdentity.current) return;
      if (result.source.sha256 !== document.sidecar.source.sha256 || result.text.sha256 !== document.sidecar.text.sha256) throw new Error('定位材料与当前识别文字不一致，结果未采用。');
      setLocations(result);
    } catch (cause) { reportError(cause, current); }
    finally { finish(current); }
  };
  const textCharacters = document ? Array.from(document.text) : [];
  const recognizedKind = document?.sidecar.recognition.kind;

  return <section className="recognition-panel" aria-label="图片和音频识别">
    <div className="recognition-heading"><div><h4>识别原件</h4><p>先识别为文字，再用于评测；可回到原图或原音频核对。</p></div>
      <label>原件类型<select aria-label="识别原件类型" value={kind} disabled={blocked || disabled || busy}
        onChange={(event) => { setKind(event.target.value as RecognitionKind); acceptSource(); }}><option value="ocr">图片</option><option value="asr">音频</option></select></label></div>
    {blocked ? <p className="workspace-input-hint" role="note">{BYOK ? '访客模式不提供本机识别，请上传已提取的文字资料。' : '公开演示不提供本机识别，请在本地运行。'}</p> : <>
      {canRestore && <div className="recognition-actions"><button type="button" className="btn btn-secondary" disabled={disabled || busy} onClick={() => { void restore(); }}>恢复记录的识别原件</button><span>从本机读取对应原件和已保存的识别文字，不重新识别。</span></div>}
      <FileImportControl key={kind} kind={kind === 'ocr' ? 'image' : 'audio'} imported={input} disabled={disabled || busy} readOnly={false} onImported={acceptSource} onBusy={onBusy}/>
      {kind === 'asr' && <p className="workspace-input-hint">仅支持 16kHz 单声道 WAV / FLAC；其他采样率或声道需先转换。</p>}
      <div className="recognition-actions"><button type="button" className="btn btn-primary" disabled={disabled || busy || !input} onClick={() => { void recognize(); }}>{busy && operation.current !== 'locations' ? '识别与读取中…' : '识别为评测文字'}</button><span>使用本机插件，不调用付费模型；不会自动重试。</span></div>
    </>}
    {busy && <div className="operation-progress" role="progressbar" aria-label="识别与原件回看进度"><span/></div>}
    {outcome && <p className={`recognition-outcome${outcome.status === 'ok' && document ? ' is-success' : ''}`} role="status">{outcome.status === 'ok' && !document ? '插件识别完成，正在核对文字与原件。' : RECOGNITION_MESSAGES[outcome.status]}</p>}
    {error && <p className="workspace-import-error" role="alert">{error}</p>}
    {document && mediaUrl && !blocked && <>
      <p className="recognition-result-note" role="status">已选用识别文字作为评测文档 · {document.sidecar.recognition.engine.name} {document.sidecar.recognition.engine.version} · {document.sidecar.spans.length} 个{recognizedKind === 'ocr' ? '识别行' : '音频片段'}</p>
      <p className="workspace-input-hint">置信度沿用引擎原值；低于 0.8 仅作回看提示，不参与门禁。未提供的置信度不填零。</p>
      <div className="recognition-review">
        <div className="recognition-original"><h5>原件回看</h5>
          {recognizedKind === 'ocr' ? <svg className="recognition-image" role="img" aria-label="识别原图与所选区域" viewBox={`0 0 ${document.sidecar.recognition.input.width} ${document.sidecar.recognition.input.height}`}>
            <image href={mediaUrl} x={0} y={0} width={document.sidecar.recognition.input.width!} height={document.sidecar.recognition.input.height!}/>
            {selected?.refs.filter((ref) => ref.box).map((ref, index) => <polygon key={`${ref.line_id}-${index}`} points={ref.box!.map((point) => point.join(',')).join(' ')} vectorEffect="non-scaling-stroke"><title>{refLabel(ref)}</title></polygon>)}
          </svg> : <><audio key={mediaUrl} ref={audio} src={mediaUrl} controls preload="metadata" aria-label="识别原音频"
            onLoadedMetadata={() => { metadataReady.current = true; if (audio.current && pendingSeek.current !== null) { audio.current.currentTime = pendingSeek.current; pendingSeek.current = null; } }}
            onError={() => { metadataReady.current = false; setError('浏览器无法播放原音频；识别文字保留，请用支持该格式的播放器核对。'); }}/>
            <p className="workspace-input-hint">选择片段会跳到起点；加载音频信息后定位，不自动播放。</p></>}
          <p className="recognition-current-location" aria-live="polite">{selected ? selected.refs.map(refLabel).join('；') || '该命中没有可用的原件位置。' : '选择识别行、片段或逐句证据的命中位置。'}</p>
        </div>
        <div className="recognition-transcript"><h5>{recognizedKind === 'ocr' ? '识别文字' : '音频片段'}</h5><ol aria-label="全部识别片段">{document.sidecar.spans.map((span, index) => <li key={index}>
          <button type="button" className="recognition-span" aria-pressed={selected?.key === `span-${index}`} onClick={() => selectRefs(`span-${index}`, [span])}>
            <span>{textCharacters.slice(span.start, span.end).join('')}</span><small>{refLabel(span)}</small><Confidence value={span.conf}/>
          </button></li>)}</ol></div>
      </div>
      <div className="recognition-evidence"><h5>逐句证据回到原件</h5>
        {canLocate ? <><p className="workspace-input-hint">定位当前已保存记录中的原引文；保留原判定。重复命中逐个列出。</p><div className="recognition-actions">
          <button type="button" className="btn btn-secondary" disabled={disabled || busy} onClick={() => { void locate(true); }}>定位逐句证据</button>
          <button type="button" className="btn btn-ghost" disabled={disabled || busy} onClick={() => { void locate(false); }}>打开已有定位</button></div></>
          : <p className="workspace-input-hint">本页先展示识别结果。用这份文字完成并保存评测后，可定位对应记录的逐句证据。</p>}
        {locations && Object.entries(locations.models).map(([model, rows]) => <details className="recognition-model" key={model} open><summary>{model} · {rows.length} 题</summary>{rows.map((row) => <div className="recognition-row" key={row.row_index}>
          <strong>第 {row.row_index + 1} 题</strong>
          {row.trace_status === 'missing' ? <p>原记录没有逐句证据（Trace 缺失）。</p> : row.trace_status === 'invalid' ? <p>原逐句证据损坏，无法定位。</p>
            : !row.statements?.length ? <p>原记录的陈述列表为空。</p> : row.statements.map((statement, statementIndex) => <div className="recognition-statement" key={statementIndex}>
              <p><strong>陈述 {statementIndex + 1}</strong> · {typeof statement.text === 'string' ? statement.text : '未提供陈述文本'}{typeof statement.verdict === 'string' && <span className="badge neutral">原判定 {statement.verdict}</span>}</p>
              {typeof statement.evidence === 'string' && <blockquote>{statement.evidence || '（空引文）'}</blockquote>}
              <p>{LOCATION_LABELS[statement.source_locations.status]}{statement.source_locations.matches.length > 0 && ` · ${statement.source_locations.matches.length} 处`}</p>
              <div className="recognition-matches">{statement.source_locations.matches.map((match, index) => {
                const key = `${model}-${row.row_index}-${statementIndex}-${index}`;
                return <button type="button" className="recognition-match" key={key} aria-label={`${model} 第${row.row_index + 1}题 第${statementIndex + 1}句 命中${index + 1}`} aria-pressed={selected?.key === key} onClick={() => selectRefs(key, match.refs)}>
                  <strong>查看命中 {index + 1}</strong><small>{match.refs.map(refLabel).join('；') || '没有可用原件位置'}</small><Confidence value={match.min_conf} missing={match.conf_missing}/>
                </button>;
              })}</div>
            </div>)}
        </div>)}</details>)}
      </div>
    </>}
  </section>;
}
