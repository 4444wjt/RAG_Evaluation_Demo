import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from '../components/Icon';
import { importInput, INPUT_EXTENSIONS } from '../api/inputFiles';
import type { ImportedInput, InputKind } from '../api/inputFiles';

interface Props {
  kind: InputKind;
  imported?: ImportedInput;
  disabled: boolean;
  readOnly: boolean;
  onImported: (input: ImportedInput) => void;
  onBusy: (busy: boolean) => void;
}

export function FileImportControl({ kind, imported, disabled, readOnly, onImported, onBusy }: Props) {
  const id = useId();
  const picker = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = disabled || readOnly || busy;
  const label = kind === 'document' ? '评测资料' : '测试集';
  useEffect(() => () => { controller.current?.abort(); }, []);

  const receive = async (files: FileList | File[]) => {
    if (locked || controller.current || !files.length) return;
    setDragging(false);
    if (files.length !== 1) { setError('请一次选择一个文件。'); return; }
    const current = new AbortController();
    controller.current = current;
    setError(null); setBusy(true); onBusy(true);
    try {
      const result = await importInput(files[0], kind, current.signal);
      if (!current.signal.aborted) onImported(result);
    } catch (cause) {
      if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : '导入未完成，请重试。');
    } finally {
      if (!current.signal.aborted) { setBusy(false); onBusy(false); }
      controller.current = null;
    }
  };

  return <div className="workspace-import">
    <input ref={picker} id={`${id}-file`} className="sr-only" type="file"
      aria-label={`从电脑选择${label}`} accept={INPUT_EXTENSIONS[kind].map((ext) => `.${ext}`).join(',')}
      disabled={locked} onChange={(event) => { const files = event.currentTarget.files; if (files) void receive(files); event.currentTarget.value = ''; }}/>
    <button type="button" className={`workspace-file-drop${dragging ? ' is-dragging' : ''}${imported ? ' has-file' : ''}`}
      disabled={locked} aria-describedby={`${id}-hint`} onClick={() => picker.current?.click()}
      onDragOver={(event) => { event.preventDefault(); if (!locked) setDragging(true); }}
      onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); if (!locked) void receive(event.dataTransfer.files); }}>
      <Icon name={busy ? 'refresh' : imported ? 'file' : 'upload'} size={24}/>
      <strong>{busy ? `正在导入${label}…` : imported ? '更换电脑文件' : '选择电脑文件'}</strong>
      <span>{readOnly ? '公开演示不接收文件' : imported ? '选择新文件以替换' : '也可以将文件拖到这里'}</span>
    </button>
    <p id={`${id}-hint`} className="workspace-input-hint">{INPUT_EXTENSIONS[kind].join(' / ').toUpperCase()} · 单个文件不超过 10MB</p>
    {readOnly ? <p className="workspace-input-hint">请使用内置示例；导入和真实评测需在本地运行。</p>
      : imported ? <div className="workspace-import-status" role="status"><Icon name="check" size={16}/><span><strong>已导入评测服务</strong><span>{imported.file_name} · {imported.size_bytes < 1024 ? `${imported.size_bytes} B` : `${(imported.size_bytes / 1024).toFixed(1)} KB`}</span></span></div>
      : <p className="workspace-input-hint">文件会发送到当前连接的评测服务，导入不会调用模型。</p>}
    {error && <p className="workspace-import-error" role="alert">{error}</p>}
  </div>;
}
