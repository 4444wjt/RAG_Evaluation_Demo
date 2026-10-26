import { useState } from 'react';
import { Icon } from '../components/Icon';
import blindspotData from '../fixtures/blindspot-check.json';
import './gate-audit.css';
import './blindspot.css';

const data = blindspotData;
type Decision = 'confirmed' | 'needs_review' | null;
export type BlindspotReview = {
  diagnosis: 'paragraph_labels' | 'uncertain' | null;
  treatmentViewed: boolean;
  sourceChecked: boolean;
  resultsChecked: boolean;
  decision: Decision;
  note: string;
};
const EMPTY: BlindspotReview = { diagnosis: null, treatmentViewed: false, sourceChecked: false, resultsChecked: false, decision: null, note: '' };
const STEPS = ['体检', '诊断', '处理', '复测与复核'];
const paragraphRows = data.rows.filter((row) => row.layer === '款级');
const controls = data.rows.filter((row) => row.type === 'control_pinpoint');
type Side = (typeof data.rows)[number]['before'] | null;

function verdict(side: Side) {
  if (!side || side.status !== 'ok') return '结果缺失';
  return ({ supported: '支持', contradicted: '矛盾', not_supported: '不支持', unsupported: '无依据' } as Record<string, string>)[side.verdict] ?? side.verdict;
}

export function reviewCanExport(review: BlindspotReview) {
  if (review.decision === 'needs_review') return review.note.trim().length > 0;
  return review.decision === 'confirmed' && review.diagnosis === 'paragraph_labels' && review.treatmentViewed && review.sourceChecked && review.resultsChecked;
}

export function exportBlindspotReview(review: BlindspotReview, savedAt: string) {
  if (!reviewCanExport(review)) throw new Error('请完成复核，或选择需要继续核查并填写原因。');
  return {
    schema: 'blindspot-review.v1', savedAt,
    source: data.source,
    contextHashes: { plain: data.contexts.plainSha256, labeled: data.contexts.labeledSha256 },
    mode: 'historical_replay', modelCalls: 0,
    reviewer: { identity: null, independentHumanVerified: false },
    review: { ...review, note: review.note.trim() },
    completed: review.decision === 'confirmed',
    archivedResults: data.stats,
    limitations: data.metadata.limitations,
  };
}

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

function LabeledText() {
  const parts = [];
  const characters = Array.from(data.contexts.labeled);
  let cursor = 0;
  for (const insertion of data.contexts.insertions) {
    parts.push(characters.slice(cursor, insertion.offset).join(''));
    parts.push(<mark key={insertion.offset}>{insertion.text}</mark>);
    cursor = insertion.offset + Array.from(insertion.text).length;
  }
  parts.push(characters.slice(cursor).join(''));
  return <>{parts}</>;
}

export function BlindspotView() {
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState(paragraphRows[0].id);
  const [review, setReview] = useState<BlindspotReview>({ ...EMPTY });
  const [notice, setNotice] = useState('');
  const row = paragraphRows.find((item) => item.id === selected)!;
  const update = (patch: Partial<BlindspotReview>) => { setReview((current) => ({ ...current, ...patch })); setNotice(''); };
  const go = (next: number) => { setStep(next); setNotice(''); };
  const exportReview = () => {
    try {
      const result = exportBlindspotReview(review, new Date().toISOString());
      download(`${JSON.stringify(result, null, 2)}\n`, 'blindspot-review.json', 'application/json;charset=utf-8');
      setNotice(review.decision === 'confirmed' ? '复核记录已导出到本机。历史裁判结果未改动。' : '待核查记录已导出，仍保留未完成状态。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '导出失败，请检查浏览器下载权限后重试。');
    }
  };

  return <div className="audit-view bs-view">
    <section className="audit-panel bs-summary" aria-labelledby="bs-summary-title">
      <div className="audit-panel-head"><div><h2 id="bs-summary-title">同一份节选，补上款号后再核对</h2><p>政府信息公开条例节选 · 第二部法规验证</p></div><span className="audit-chip audit-chip--warning">历史回放 · 不调用模型</span></div>
      <table className="bs-score-table" aria-label="历史体检与复测结果"><thead><tr><th scope="col">检查项</th><th scope="col">原节选</th><th scope="col">补标签后</th></tr></thead><tbody>
        <tr><th scope="row">款级错误检出</th><td className="bs-before">{data.stats.before.detected} / {data.stats.before.total}</td><td className="bs-after">{data.stats.after.detected} / {data.stats.after.total}</td></tr>
        <tr><th scope="row">正确对照误报</th><td>{data.stats.before.falsePositives} / {data.stats.before.controls}</td><td>{data.stats.after.falsePositives} / {data.stats.after.controls}</td></tr>
      </tbody></table>
      <p className="bs-boundary">这些题来自 {data.stats.contentUnits} 个内容单元，衍生变体并非独立样本。以下操作读取已有归档，不启动新评测，不改变质量门禁。</p>
    </section>

    <nav className="bs-steps" aria-label="体检闭环步骤">{STEPS.map((label, index) => <button key={label} onClick={() => go(index)} aria-current={step === index ? 'step' : undefined}><span>{index + 1}</span>{label}<Icon name="chevron" size={14}/></button>)}</nav>

    <section className="audit-panel bs-workbench" aria-label={STEPS[step]}>
      {step === 0 && <>
        <h2>体检：先看原节选下漏掉了什么</h2>
        <p className="bs-description">这组题只改变引用位置。先读陈述，再核对裁判为什么把错误引用判为有依据。</p>
        <label className="bs-case-label" htmlFor="bs-case">选择款级题</label><select id="bs-case" value={selected} onChange={(event) => setSelected(event.target.value)}>{paragraphRows.map((item) => <option key={item.id} value={item.id}>{item.cited} · {verdict(item.before)}</option>)}</select>
        <dl className="bs-case-meta"><div><dt>题面引用</dt><dd>{row.cited}</dd></div><div><dt>内容实际来自</dt><dd>{row.contentFrom}</dd></div></dl>
        <blockquote className="bs-statement">{row.statement}</blockquote>
        <div className="bs-reason"><h3>原节选下的裁判判断：{verdict(row.before)}</h3><p>{row.before.reason || '归档没有理由文本。'}</p></div>
        <button className="btn btn-primary" onClick={() => go(1)}>查看诊断依据<Icon name="arrow" size={15}/></button>
      </>}

      {step === 1 && <>
        <h2>诊断：款边界只有换行，没有完整款号</h2>
        <p className="bs-description">原节选保留条号，款靠换行区分。本次检验的处理方向是把款边界显式标出来；这份对照不能证明它是漏检的唯一原因。</p>
        <pre className="bs-document" aria-label="诊断所用原节选">{data.contexts.plain}</pre>
        <fieldset className="bs-choice"><legend>记录本次处理方向</legend>
          <label><input type="radio" name="bs-diagnosis" checked={review.diagnosis === 'paragraph_labels'} onChange={() => update({ diagnosis: 'paragraph_labels' })}/>采用补款号标签的处理方向</label>
          <label><input type="radio" name="bs-diagnosis" checked={review.diagnosis === 'uncertain'} onChange={() => update({ diagnosis: 'uncertain', decision: null })}/>原因仍不确定，保留待核查</label>
        </fieldset>
        <button className="btn btn-primary" onClick={() => go(2)}>查看标签处理<Icon name="arrow" size={15}/></button>
      </>}

      {step === 2 && <>
        <div className="audit-panel-head"><div><h2>处理：只插入标签，原正文保留</h2><p>金色标记是归档中的实际插入位置，不是重新生成的法规。</p></div><span className={`audit-chip audit-chip--${data.contexts.bodyBytesUnchanged ? 'success' : 'danger'}`}>{data.contexts.bodyBytesUnchanged ? '移除标签后，原正文逐字节一致' : '正文一致性未通过'}</span></div>
        <div className="bs-documents"><div><h3>原节选 · {data.contexts.plainBytes} 字节</h3><pre className="bs-document" aria-label="处理前原节选">{data.contexts.plain}</pre></div><div><h3>补标签后 · {data.contexts.labeledBytes} 字节</h3><pre className="bs-document" aria-label="补标签后节选"><LabeledText/></pre></div></div>
        <div className="bs-actions"><button className="btn btn-secondary" onClick={() => download(data.contexts.labeled, 'information-disclosure-labeled.txt', 'text/plain;charset=utf-8')}><Icon name="file" size={15}/>下载归档标签文本</button><button className="btn btn-primary" onClick={() => { update({ treatmentViewed: true }); go(3); }}>已查看处理，核对归档复测<Icon name="arrow" size={15}/></button></div>
        <p className="audit-note">下载用于检查本次处理；换一份资料仍需另行验证，不自动套用这次裁判结果。</p>
      </>}

      {step === 3 && <>
        <h2>复测：逐题核对归档结果</h2><p className="bs-description">同一裁判，题目和请求参数保持一致，只替换有无款号标签的资料。下表来自历史记录，本页没有重新请求裁判。</p>
        <div className="audit-table-scroll"><table className="bs-result-table" aria-label="款级逐题复测"><thead><tr><th scope="col">引用位置</th><th scope="col">内容实际来自</th><th scope="col">原节选判断</th><th scope="col">补标签后判断</th></tr></thead><tbody>{paragraphRows.map((item) => <tr key={item.id}><th scope="row">{item.cited}</th><td>{item.contentFrom}</td><td><span className={`audit-chip audit-chip--${item.before.verdict === 'supported' ? 'danger' : 'success'}`}>{verdict(item.before)}</span></td><td>{verdict(item.after)}</td></tr>)}</tbody></table></div>
        <details className="bs-controls"><summary>查看 {data.stats.before.controls} 条正确对照</summary><ul>{controls.map((item) => <li key={item.id}><strong>{item.cited}</strong><span>{verdict(item.before)} → {verdict(item.after)}</span><p>{item.statement}</p></li>)}</ul></details>
        <fieldset className="bs-choice"><legend>本页复核记录</legend>
          <label><input type="checkbox" checked={review.sourceChecked} onChange={(event) => update({ sourceChecked: event.target.checked })}/>已核对原文和标签改动</label>
          <label><input type="checkbox" checked={review.resultsChecked} onChange={(event) => update({ resultsChecked: event.target.checked })}/>已核对逐题结果和正确对照</label>
          <label htmlFor="bs-decision">复核意见</label><select id="bs-decision" value={review.decision ?? ''} onChange={(event) => update({ decision: (event.target.value || null) as Decision })}><option value="">尚未选择</option><option value="confirmed">确认本次归档对照，保留适用边界</option><option value="needs_review">需要继续核查</option></select>
          <label htmlFor="bs-note">补充说明{review.decision === 'needs_review' ? '（待核查时必填）' : '（可选）'}</label><textarea id="bs-note" rows={3} maxLength={4000} value={review.note} onChange={(event) => update({ note: event.target.value })}/>
        </fieldset>
        <div className="bs-actions"><button className="btn btn-primary" disabled={!reviewCanExport(review)} onClick={exportReview}><Icon name="file" size={15}/>导出复核记录</button><span className="audit-note">{reviewCanExport(review) ? '记录仅下载到本机，不上传。' : '确认对照需先记录处理方向、查看处理并完成两项核对；也可填写待核查原因。'}</span></div>
        {notice && <p className="bs-export-notice" role="status">{notice}</p>}
        <p className="audit-note">复核意见不等于独立人工标注，也不会覆盖历史裁判结果。未确认的记录保留为待核查。</p>
      </>}
    </section>

    <section className="audit-panel bs-evidence" aria-labelledby="bs-evidence-title"><h2 id="bs-evidence-title">来源与边界</h2>
      <p>单部法规、单裁判、单次运行。只能说明本组引用扰动题中的标签干预结果，不代表真实业务效果或算法优于简单基线。</p>
      <details><summary>查看归档与跑前登记</summary>
        <dl><div><dt>归档</dt><dd><code>{data.source.archivePath}</code></dd></div><div><dt>请求／实际裁判名</dt><dd><code>{data.source.modelRequested} / {data.source.modelReturned}</code></dd></div><div><dt>历史调用</dt><dd>{data.stats.calls} 次；本页新增 0 次</dd></div><div><dt>正文一致性</dt><dd>{data.contexts.bodyBytesUnchanged ? '逆向移除标签，字节一致' : '未通过'}</dd></div><div><dt>可比请求</dt><dd>{data.contexts.pairedRequestsMatch ? '除资料标签外一致' : '未通过'}</dd></div><div><dt>跑前登记 SHA-256</dt><dd><code>{data.source.registrationSha256}</code></dd></div></dl>
        <ul>{data.thresholds.map((threshold) => <li key={threshold.id}>{threshold.label}：{threshold.passed ? '满足' : '未满足'}</li>)}</ul>
        <p className="audit-note">登记原文位于内部工作目录，不生成无法访问的公开链接。可用以下脚本核对本页数据与归档的绑定。</p>
        <pre className="bs-command">python tools/competition/build_blindspot_fixture.py --check</pre>
        <p className="audit-note">历史实验脚本：tools/competition/statute_blindspot.py、tools/competition/statute_labeled.py。运行模型实验另需配置与授权；本页仅离线回放。</p>
      </details>
    </section>
  </div>;
}
