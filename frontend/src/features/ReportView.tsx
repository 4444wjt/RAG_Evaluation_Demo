import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import type { EvaluationResult } from '../api/types';
import { fetchEvaluationReport } from '../api/evaluations';
import { saveKUZip as downloadBlob } from '../api/knowledgeUpdate';
import { Icon } from '../components/Icon';
import { BYOK, READ_ONLY } from '../config';
import { JudgeProvenance } from './JudgeProvenance';
import './details.css';

/** Public builds show only the file name: the frozen snapshot records absolute paths from the author's machine. */
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

type ImportedReport = { result: EvaluationResult | null; name: string; document: string; saved?: boolean };
type Notice = { kind: 'success' | 'error'; text: string };

function readFile(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('文件内容无法读取'));
    reader.onerror = () => reject(reader.error ?? new Error('文件读取失败'));
    reader.onabort = () => reject(new Error('文件读取已取消'));
    reader.readAsText(file);
  });
}

function isolatedDocument(source: string): string {
  // Template content stays inert while the local file is inspected, including its resources.
  const template = document.createElement('template');
  template.innerHTML = source;
  template.content.querySelectorAll('script, noscript, iframe, frame, object, embed, base, link, meta[http-equiv]').forEach((node) => node.remove());
  template.content.querySelectorAll('*').forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on') || ['href', 'xlink:href', 'action', 'formaction', 'srcset', 'poster', 'background', 'ping', 'srcdoc'].includes(name)) {
        element.removeAttribute(attribute.name);
      } else if (name === 'src' && !(element.tagName === 'IMG' && /^data:image\//i.test(attribute.value))) {
        element.removeAttribute(attribute.name);
      }
    }
  });
  const policy = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'";
  return `<!doctype html><html lang="zh-CN"><head><meta http-equiv="Content-Security-Policy" content="${policy}"><meta charset="UTF-8"></head><body>${template.innerHTML}</body></html>`;
}

export function ReportView({ result }: { result: EvaluationResult | null }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const [report, setReport] = useState<ImportedReport | null>(null);
  const [reading, setReading] = useState(false);
  const [importError, setImportError] = useState('');
  const [copyNotice, setCopyNotice] = useState<Notice | null>(null);
  const visibleReport = report?.result === result ? report : null;

  useEffect(() => {
    const token = ++generation.current;
    const controller = new AbortController();
    setReport(null);
    setReading(false);
    setImportError('');
    setCopyNotice(null);
    if (result?.record_id && !READ_ONLY) {
      setReading(true);
      fetchEvaluationReport(result.record_id, controller.signal).then((source) => {
        if (generation.current === token) setReport({ result, name: '已保存的 HTML 报告', document: isolatedDocument(source), saved: true });
      }).catch((error) => {
        if (generation.current === token) setImportError(error instanceof Error ? error.message : '历史报告读取失败。');
      }).finally(() => {
        if (generation.current === token) setReading(false);
      });
    }
    return () => { generation.current += 1; controller.abort(); };
  }, [result]);

  async function importReport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;
    setImportError('');
    if (!/\.html$/i.test(file.name)) {
      setImportError('请选择 .html 格式的本地报告。');
      return;
    }
    const token = ++generation.current;
    setReading(true);
    try {
      const source = await readFile(file);
      if (generation.current !== token) return;
      if (!source.trim()) throw new Error('所选 HTML 文件为空，请选择已有报告。');
      const document = isolatedDocument(source);
      if (generation.current !== token) return;
      setReport({ result, name: file.name, document });
    } catch (error) {
      if (generation.current === token) setImportError(error instanceof Error ? error.message : '报告读取失败，请重新选择文件。');
    } finally {
      if (generation.current === token) setReading(false);
    }
  }

  async function copyPath(path: string) {
    const token = generation.current;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('浏览器未提供剪贴板权限，请选中路径手动复制。');
      await navigator.clipboard.writeText(path);
      if (generation.current === token) setCopyNotice({ kind: 'success', text: '已复制本地路径。' });
    } catch {
      if (generation.current === token) setCopyNotice({ kind: 'error', text: '复制失败，请选中路径手动复制。' });
    }
  }

  function clearReport() {
    generation.current += 1;
    setReport(null);
    setReading(false);
    setImportError('');
  }

  function downloadReport() {
    if (!visibleReport) return;
    const name = visibleReport.saved && result?.record_id
      ? `veriscope-report-${result.record_id}.html` : fileName(visibleReport.name);
    try {
      downloadBlob(new Blob([visibleReport.document], { type: 'text/html;charset=utf-8' }), name);
      setCopyNotice({ kind: 'success', text: '已发起当前预览报告下载，请在浏览器下载列表确认。' });
    } catch {
      setCopyNotice({ kind: 'error', text: '报告下载未完成，请重试或检查浏览器下载权限。' });
    }
  }

  const paths = [
    { label: 'HTML 报告', value: result?.html_path, icon: 'file' as const },
    { label: '结果图表', value: result?.chart_path, icon: 'chart' as const },
  ];

  return <section className="report-view" aria-label="评测报告">
    {result && <JudgeProvenance rows={result.summary}/>}
    <header className="report-toolbar">
      <p id="report-view-title" className="muted">{result?.record_id ? '查看当前记录保存的 HTML 报告，也可手动导入本地报告。' : '导入本地 HTML 报告，查看图表与详细评测记录。'}</p>
      <button type="button" className="btn btn-primary" disabled={reading} onClick={() => fileInput.current?.click()}><Icon name="folder" size={16} />{reading ? '正在读取…' : '导入 HTML 报告'}</button>
      <input ref={fileInput} className="report-file-input" type="file" accept=".html,text/html" aria-label="选择本地 HTML 报告" onChange={importReport} />
    </header>

    {!BYOK && <div className="report-path-panel">
      <div className="report-path-heading"><h3>本地产物路径</h3><span>来自当前结果</span></div>
      <div className="report-paths">{paths.map((path) => <div className="report-path-row" key={path.label}>
        <div className="report-path-label"><Icon name={path.icon} size={17} /><span>{path.label}</span></div>
        {path.value && READ_ONLY ? <code className="report-path-value" tabIndex={0}>{fileName(path.value)}</code> : path.value ? <><code className="report-path-value" tabIndex={0}>{path.value}</code><button type="button" className="btn btn-ghost report-copy-button" aria-label={`复制${path.label}本地路径`} onClick={() => void copyPath(path.value!)}><Icon name="copy" size={15} /><span>复制路径</span></button></> : <span className="report-path-missing">{result ? '当前结果未记录路径' : '尚未载入评测结果'}</span>}
      </div>)}</div>
      <p className="report-path-note">{READ_ONLY ? '这些是评测时在本机生成的产物，公开站点不提供下载；可导入你自己的 HTML 报告预览。' : result?.record_id ? '预览从当前记录读取；这些原始路径不用于加载报告。' : '路径指向本地文件。请从本地选择报告导入；文件可能被后续评测覆盖。'}</p>
    </div>}
    {copyNotice && <p className={`report-notice report-notice--${copyNotice.kind}`} role="status">{copyNotice.text}</p>}

    {importError && <div className="report-import-error" role="alert"><Icon name="alert" size={17} /><span>{importError}</span></div>}

    {visibleReport ? <div className="report-preview">
      <div className="report-preview-heading">
        <div className="report-filename"><Icon name="file" size={18} /><span>{visibleReport.name}</span><span className="badge report-local-badge">{visibleReport.saved ? '当前记录' : '本地文件'}</span></div>
        <button type="button" className="btn btn-secondary" onClick={downloadReport}><Icon name="file" size={15} />下载当前预览 HTML 报告</button>
        <button type="button" className="btn btn-ghost" onClick={clearReport}>清除预览</button>
      </div>
      <div className="report-source-notice"><Icon name={visibleReport.saved ? 'check' : 'alert'} size={15} /><span>{visibleReport.saved ? '按当前记录 ID 读取，未重新执行评测' : '手动导入，未自动核验与当前结果对应'}</span></div>
      <iframe className="report-frame" title={visibleReport.saved ? '历史报告预览' : `本地报告预览：${visibleReport.name}`} sandbox="" referrerPolicy="no-referrer" srcDoc={visibleReport.document} />
      <div className="report-preview-footer"><Icon name="shield" size={14} /><span>隔离预览，仅显示内联样式和内嵌图片；脚本与外部内容已禁用。</span></div>
    </div> : <div className="empty-state report-empty">
      <span className="details-empty-icon"><Icon name="file" size={29} /></span>
      <h3>{reading ? result?.record_id ? '正在读取已保存报告' : '正在读取本地报告' : '导入报告以开始预览'}</h3>
      <p>选择已有的 .html 文件，报告仅在当前浏览器中读取。</p>
      <span className="report-empty-note">手动导入，未自动核验与当前结果对应</span>
    </div>}
  </section>;
}
