import { useEffect, useRef, useState } from 'react';
import { createImprovement, downloadImprovement, verifyImprovement } from '../api/answerImprovement';
import type { ImprovementRecord, ImprovementVerification } from '../api/answerImprovement';
import type { EvaluationResult, ResultSource } from '../api/types';
import { READ_ONLY, STATIC_SITE } from '../config';
import { modelName } from '../utils/format';
import './answer-improvement.css';

const text = (value: unknown) => typeof value === 'string' ? value : value === null || value === undefined ? '未记录' : JSON.stringify(value);
const statusLabel = { verified_candidate: '字面与补丁复核一致', no_candidate: '未生成候选', review: '待人工复核' };

export function AnswerImprovementView({ result, source, initialModel }: { result: EvaluationResult | null; source: ResultSource | null; initialModel?: string }) {
  const models = result ? Object.keys(result.model_dfs) : [];
  const [model, setModel] = useState(() => models.includes(initialModel ?? '') ? initialModel! : models[0] ?? '');
  const [record, setRecord] = useState<ImprovementRecord | null>(null);
  const [rowIndex, setRowIndex] = useState(0);
  const [verification, setVerification] = useState<ImprovementVerification | null>(null);
  const [verificationIncomplete, setVerificationIncomplete] = useState(false);
  const [downloaded, setDownloaded] = useState<{ sha256: string; size: number } | null>(null);
  const [busy, setBusy] = useState<'generate' | 'verify' | 'download' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<{ controller: AbortController; result: EvaluationResult | null; model: string } | null>(null);
  const active = useRef({ result, model });
  active.current = { result, model };

  const clear = () => { request.current?.controller.abort(); request.current = null; setRecord(null); setVerification(null); setVerificationIncomplete(false); setDownloaded(null); setBusy(null); setError(null); setRowIndex(0); };
  useEffect(() => {
    clear();
    setModel(models.includes(initialModel ?? '') ? initialModel! : models[0] ?? '');
    return () => { request.current?.controller.abort(); request.current = null; };
  }, [result, initialModel]);

  const boundRecord = record?.parent_record_id === result?.record_id && record?.model_name === model ? record : null;
  const reason = READ_ONLY || STATIC_SITE ? '请在本地服务中操作，公开演示不生成候选。'
    : source === 'demo' ? '当前为演示快照，请打开自己的评测记录后生成候选。'
      : !result?.record_id ? '请先保存记录或通过历史记录导入完整评测 JSON。'
        : !model ? '当前记录没有可选模型。' : null;
  const rows = result?.model_dfs[model] ?? [];
  const item = boundRecord?.items.find((row) => row.row_index === rowIndex);
  const original = item ? item.original_answer : rows[rowIndex]?.answer;
  const contexts = item ? item.contexts : rows[rowIndex]?.contexts;

  const run = async (kind: 'generate' | 'verify' | 'download') => {
    if (reason || busy || !result?.record_id || (kind !== 'generate' && !boundRecord)) return;
    const current = { controller: new AbortController(), result, model };
    request.current?.controller.abort(); request.current = current;
    const stillCurrent = () => request.current === current && !current.controller.signal.aborted
      && active.current.result === current.result && active.current.model === current.model;
    setBusy(kind); setError(null);
    if (kind === 'generate') { setRecord(null); setVerification(null); setVerificationIncomplete(false); setDownloaded(null); }
    if (kind === 'verify') { setVerification(null); setVerificationIncomplete(true); }
    try {
      if (kind === 'generate') {
        const next = await createImprovement(result.record_id, model, current.controller.signal);
        if (stillCurrent()) { setRecord(next); setRowIndex(next.items[0]?.row_index ?? 0); }
      } else if (kind === 'verify') {
        const next = await verifyImprovement(boundRecord!.id, current.controller.signal);
        if (stillCurrent()) { setVerification(next); setVerificationIncomplete(false); }
      } else {
        const next = await downloadImprovement(boundRecord!.id, current.controller.signal);
        if (stillCurrent()) setDownloaded(next);
      }
    } catch (failure) { if (stillCurrent()) setError(failure instanceof Error ? failure.message : '操作未完成。'); }
    finally { if (stillCurrent()) { setBusy(null); request.current = null; } }
  };

  return <div className="ai-view">
    <section className="ai-panel" aria-labelledby="ai-scope-title">
      <div className="section-heading"><div><h2 id="ai-scope-title">数字局部候选</h2><p>来源需为单句陈述；仅对数字外逐字相同、同单位且唯一不同的数量提出一处替换，原回答与评测记录保留。</p></div><span className="subtle-tag">不调用模型</span></div>
      <div className="ai-gate"><strong>{result ? `原始门禁 ${result.gate_passed ? 'PASS' : 'FAIL'}` : '尚未加载原始门禁'}</strong><span>新门禁与 Ragas：未重新评分</span></div>
      {result && <details><summary>原始门禁说明与阈值</summary><p>{result.gate_message}</p><p>{`忠实度阈值：${typeof result.faith_threshold === 'number' ? result.faith_threshold : '未记录'}；上下文精度阈值：${typeof result.ctx_prec_threshold === 'number' ? result.ctx_prec_threshold : '未记录'}`}</p></details>}
      <div className="ai-controls"><label>候选模型<select aria-label="候选模型" value={model} onChange={(event) => { clear(); setModel(event.target.value); }}>{models.map((name) => <option key={name} value={name}>{modelName(name)}</option>)}</select></label><button className="btn btn-primary" disabled={Boolean(reason) || busy !== null} onClick={() => { void run('generate'); }}>{busy === 'generate' ? '正在生成候选…' : '生成数字局部候选'}</button></div>
      {reason && <p className="muted">{reason}</p>}
      {error && <p className="ai-error" role="alert">{error}</p>}
      {busy && <p role="status">{busy === 'generate' ? '正在按完整模型记录逐行检查…' : busy === 'verify' ? '正在重新核验副记录…' : '正在下载完整副记录…'}</p>}
      {boundRecord && <><dl className="ai-counts" aria-label="全部行统计"><div><dt>全部回答</dt><dd>{boundRecord.counts.total_rows}</dd></div><div><dt>已提出替换</dt><dd>{boundRecord.counts.changed_rows}</dd></div><div><dt>字面复核一致</dt><dd>{boundRecord.counts.verified_rows}</dd></div><div><dt>未生成候选</dt><dd>{boundRecord.counts.no_candidate_rows}</dd></div><div><dt>待人工复核</dt><dd>{boundRecord.counts.review_rows}</dd></div></dl>
        <div className="ai-actions"><button className="btn btn-secondary" disabled={busy !== null} onClick={() => { void run('verify'); }}>重新核验字面与补丁</button><button className="btn btn-secondary" disabled={busy !== null} onClick={() => { void run('download'); }}>下载完整候选记录</button></div>
        {verification && <div className={verification.status === 'failed' ? 'ai-error' : 'ai-verification'} role="status"><strong>{`重新核验：${verification.status === 'verified' ? '字面与补丁复核一致' : '待人工复核'}`}</strong>{verification.checks.some((check) => !check.passed) && <ul>{verification.checks.filter((check) => !check.passed).map((check) => <li key={check.code}>{check.code}</li>)}</ul>}</div>}
        {verificationIncomplete && <div className="ai-error" role="status"><strong>{busy === 'verify' ? '本次复核进行中，待复核' : '本次复核未完成，待复核'}</strong></div>}
        {downloaded && <p className="ai-download" role="status">{`已下载 ${downloaded.size} 字节 · 文件 SHA-256：${downloaded.sha256}`}</p>}
      </>}
    </section>
    {rows.length > 0 && <section className="ai-panel" aria-labelledby="ai-item-title"><div className="section-heading"><h2 id="ai-item-title">逐题查看</h2><label>回答<select aria-label="候选回答" value={rowIndex} onChange={(event) => setRowIndex(Number(event.target.value))}>{rows.map((row, index) => <option key={index} value={index}>{`${index + 1}. ${text(row.question)}`}</option>)}</select></label></div>
      <p className="ai-question">{text(item?.question ?? rows[rowIndex]?.question)}</p>
      {item && <p className={`ai-status ai-status-${verificationIncomplete || verification?.status === 'failed' ? 'review' : item.status}`}>{verificationIncomplete ? `${busy === 'verify' ? '本次复核进行中' : '本次复核未完成'}，待复核（保留生成时的候选）` : verification?.status === 'failed' ? '本次重核未一致，待人工复核（保留生成时的候选）' : statusLabel[item.status]}</p>}
      <div className="ai-comparison"><div><h3>原回答</h3><pre className="ai-answer">{text(original)}</pre></div><div><h3>候选回答</h3><pre className="ai-answer">{item?.candidate_answer ?? (boundRecord ? '本题未生成候选。' : '生成候选后显示。')}</pre></div></div>
      {item && item.edits.length > 0 && <section className="ai-patches"><h3>替换与来源</h3>{item.edits.map((edit, index) => { const chars = Array.from(text(original)); return <div key={index} className="ai-patch"><p><span>数字位置 [{edit.start}, {edit.end}) · </span><del>{edit.before}</del><span aria-hidden="true"> → </span><ins>{edit.after}</ins></p><pre>{chars.slice(0, edit.start).join('')}<mark>{edit.after}</mark>{chars.slice(edit.end).join('')}</pre><p>{`来源片段 ${edit.context_index + 1}`}</p><blockquote>{edit.evidence}</blockquote></div>; })}</section>}
      {item && <section><h3>未解决与人工复核</h3>{item.unresolved_reasons.length > 0 ? <ul>{item.unresolved_reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>未列出其他具体问题；语义正确性仍未评估。</p>}</section>}
      <details><summary>查看本题实际上下文</summary>{Array.isArray(contexts) ? contexts.map((context, index) => <pre className="ai-context" key={index}>{text(context)}</pre>) : <pre className="ai-context">{text(contexts)}</pre>}</details>
    </section>}
    <section className="ai-panel ai-boundary"><h2>核验范围</h2><p>只检查字面事实匹配、单处补丁完整性与输入绑定；多句来源或适用条件不明时保留原答。语义正确性、检索完整性和回答质量均未评估。</p><p>上下文取自原记录保存或用户导入，来源真实性未核。仅绑定字节，不证明语义正确。</p></section>
  </div>;
}
