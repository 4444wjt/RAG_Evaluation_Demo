import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { fetchEvaluationHistory, fetchEvaluationRecord, importEvaluationRecord, readRecordFile } from '../api/evaluations';
import type { EvaluationHistory as History, EvaluationRecord } from '../api/types';
import { Icon } from '../components/Icon';
import { modelName, seconds } from '../utils/format';
import './evaluation-history.css';

export function EvaluationHistory({ disabled, onOpen, onClose }: { disabled: boolean; onOpen: (record: EvaluationRecord) => void; onClose: () => void }) {
  const [history, setHistory] = useState<History | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function refresh() {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setBusy(true);
    setError('');
    try {
      const data = await fetchEvaluationHistory(next.signal);
      if (!next.signal.aborted) setHistory(data);
    } catch (reason) {
      if (!next.signal.aborted) setError(reason instanceof Error ? reason.message : '历史列表读取失败。');
    } finally {
      if (!next.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    return () => controller.current?.abort();
  }, []);

  async function open(id: string) {
    if (busy || disabled) return;
    const next = new AbortController();
    controller.current = next;
    setBusy(true);
    setError('');
    try {
      const record = await fetchEvaluationRecord(id, next.signal);
      if (!next.signal.aborted) onOpen(record);
    } catch (reason) {
      if (!next.signal.aborted) setError(reason instanceof Error ? reason.message : '历史记录读取失败。');
    } finally {
      if (!next.signal.aborted) setBusy(false);
    }
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file || busy || disabled) return;
    const next = new AbortController();
    controller.current = next;
    setBusy(true);
    setError('');
    try {
      let value: unknown;
      try { value = JSON.parse(await readRecordFile(file)); }
      catch { throw new Error('JSON 文件无法读取，请选择完整评测结果或导出的记录包。'); }
      if (next.signal.aborted) return;
      const record = await importEvaluationRecord(value, next.signal);
      if (!next.signal.aborted) onOpen(record);
    } catch (reason) {
      if (!next.signal.aborted) setError(reason instanceof Error ? reason.message : '历史记录导入失败。');
    } finally {
      if (!next.signal.aborted) setBusy(false);
    }
  }

  return <section id="evaluation-history" className="evaluation-history" aria-label="历史记录" aria-busy={busy}>
    <header className="history-header"><div><h2>历史记录</h2><p>保存于当前本地服务；打开记录不会重新执行评测。</p></div><div className="history-actions">
      <button type="button" className="btn btn-secondary" disabled={busy || disabled} onClick={() => input.current?.click()}><Icon name="upload" size={15}/>导入记录</button>
      <button type="button" className="btn btn-ghost" disabled={busy || disabled} onClick={() => { void refresh(); }}>刷新列表</button>
      <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="关闭历史记录">关闭</button>
      <input ref={input} className="sr-only" type="file" accept=".json,application/json" aria-label="选择评测记录 JSON" disabled={busy || disabled} onChange={importFile}/>
    </div></header>
    {error && <p className="history-error" role="alert">{error}</p>}
    {busy && <p className="muted" role="status">正在读取历史记录…</p>}
    {history && history.unreadable_count > 0 && <p className="history-warning">{history.unreadable_count} 条记录无法读取，未显示为有效历史。</p>}
    {history && history.records.length === 0 && <p className="muted">尚无保存的记录</p>}
    {history && history.records.length > 0 && <ul className="history-list">{history.records.map((record) => <li key={record.id}>
      <div className="history-record-main"><strong>{record.document_name || '未记录资料名称'}</strong><span>{record.created_at} · {record.source === 'imported' ? '导入记录' : '评测记录'}</span><span>{record.models.map(modelName).join('、') || '未记录模型'} · 总耗时 {seconds(record.total_elapsed_seconds)}</span>
      {record.artifact_warnings.length > 0 && <span className="history-warning">{record.artifact_warnings.join('；')}</span>}</div>
      <span className={`badge ${record.gate_passed ? 'success' : 'warning'}`}>原始门禁 {record.gate_passed ? 'PASS' : 'FAIL'}</span><span className="muted">{record.has_report ? '含报告' : '无报告'}</span>
      <button type="button" className="btn btn-secondary" disabled={busy || disabled} onClick={() => { void open(record.id); }} aria-label={`打开记录 ${record.document_name || record.id} ${record.created_at}`}>打开</button>
    </li>)}</ul>}
    <p className="history-note">支持导出的 JSON 包和旧版完整结果 JSON。仅有摘要或 HTML 报告的旧记录不能恢复逐题结果。</p>
  </section>;
}
