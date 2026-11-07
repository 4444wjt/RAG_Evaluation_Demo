import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { READ_ONLY, STATIC_SITE } from '../config';
import { checkFinanceReview, downloadFinanceReview, financeFindings, readFinanceFile } from '../api/financeReview';
import type { FinanceBundle, FinanceReport, FinanceRequest } from '../api/financeReview';
import examples from '../fixtures/finance-examples.json';
import './finance-review.css';

const STATUS: Record<FinanceReport['status'], string> = {
  findings: '检出规则问题', no_error_found: '规则未检出，仍需核对',
  review: '需要复核，未完成核对', invalid_input: '输入不完整，未完成核对',
};
const SEVERITY = { error: '错误', warn: '提示', info: '信息' };

export function FinanceReviewView() {
  const locked = READ_ONLY || STATIC_SITE;
  const [source, setSource] = useState('');
  const [answer, setAnswer] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [sample, setSample] = useState<string | null>(null);
  const [current, setCurrent] = useState<FinanceBundle | null>(null);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [notice, setNotice] = useState('');
  const task = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const selected = examples.examples.find((item) => item.id === sample);

  useEffect(() => () => { task.current += 1; controller.current?.abort(); }, []);

  const retire = () => {
    task.current += 1; controller.current?.abort(); controller.current = null;
    setCurrent(null); setFailure(''); setNotice(''); setBusy(false); setReading(false);
  };
  const changeSource = (value: string) => { retire(); setSource(value); setConfirmed(false); setSample(null); };
  const changeAnswer = (value: string) => { retire(); setAnswer(value); setSample(null); };
  const loadExample = (id: string) => {
    const next = examples.examples.find((item) => item.id === id);
    if (!next) return;
    retire(); setSource(next.source ?? examples.source); setAnswer(next.answer); setConfirmed(false); setSample(next.id);
  };

  const importSource = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
    if (!file || locked) return;
    retire(); setConfirmed(false); setSample(null); setReading(true);
    const token = task.current;
    try {
      const text = await readFinanceFile(file);
      if (token !== task.current) return;
      setSource(text); setNotice(`已导入 ${file.name}，请确认行结构。`);
    } catch (error) { if (token === task.current) setFailure(error instanceof Error ? error.message : '文件未能导入。'); }
    finally { if (token === task.current) setReading(false); }
  };

  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (locked || busy || reading || !confirmed || !source.trim() || !answer.trim()) return;
    retire(); setBusy(true);
    const token = task.current;
    const cancellation = new AbortController(); controller.current = cancellation;
    const input: FinanceRequest = { schemaVersion: 1, sourceFormat: 'article-lines-v1', engine: 'base', source, answer, contexts: [source], question: '' };
    try {
      const bundle = await checkFinanceReview(input, cancellation.signal);
      if (token === task.current) setCurrent(bundle);
    } catch (error) { if (token === task.current) setFailure(error instanceof Error ? error.message : '未取得核对结果。'); }
    finally { if (token === task.current) { setBusy(false); controller.current = null; } }
  };
  const download = (format: 'bundle' | 'request') => {
    setFailure(''); setNotice('');
    if (!current) return;
    try { downloadFinanceReview(current, format); setNotice('已触发下载，请在浏览器下载列表确认文件。'); }
    catch { setFailure('下载未完成，请检查浏览器下载设置后重试。'); }
  };

  const report = current?.report;
  const findings = report ? financeFindings(report) : [];
  const recoveries = report?.result && 'recoveries' in report.result ? report.result.recoveries.filter((item) => item.recovered !== null) : [];

  return <div className="fr-view">
    <section className="fr-intro" aria-labelledby="fr-intro-title">
      <div><p className="fr-eyebrow">给定制度原文 · 离线规则</p><h2 id="fr-intro-title">把稿件里的引用带回原文</h2><p>核对条号、数额、期限和情态词。业务条件、产品性质与法律适用仍需人工判断。</p></div>
      <div className="fr-examples" role="group" aria-label="操作示例"><span>试一条既有材料</span>{examples.examples.map((item) => <button type="button" key={item.id} className="btn btn-secondary" aria-pressed={sample === item.id} onClick={() => loadExample(item.id)}>{item.label}</button>)}</div>
    </section>
    {locked && <p className="fr-note">公开演示和静态页面不接收文件。请在本地服务中使用此入口。</p>}
    {selected && <p className="fr-example-note"><strong>{selected.label}：</strong>{selected.note}<span>{examples.title} · <a href={examples.sourceUrl} target="_blank" rel="noopener noreferrer">查看既有来源 PDF</a></span></p>}

    <form className="fr-workspace" onSubmit={(event) => { void run(event); }}>
      <section className="fr-panel fr-source" aria-label="原文资料区域">
        <div className="fr-panel-heading"><label id="fr-source-title" htmlFor="fr-source">制度原文</label><label className={`btn btn-secondary fr-upload${locked ? ' fr-disabled' : ''}`}><span>{reading ? '正在导入…' : '导入 TXT'}</span><input type="file" accept=".txt,text/plain" aria-label="导入 UTF-8 TXT" disabled={locked || reading} onChange={(event) => { void importSource(event); }}/></label></div>
        <p id="fr-format" className="fr-note">第N条标题与正文同一行；每行一款；“（一）”起始行为上一款的项。空行结束当前条。</p>
        <textarea id="fr-source" aria-label="制度原文" aria-describedby="fr-format" value={source} onChange={(event) => changeSource(event.target.value)} placeholder="粘贴已确认行结构的制度原文，或先选择上方示例。" spellCheck={false}/>
        <p className="fr-note">本次以全部原文作为核对资料，不执行检索；不自动合并折行或补造条款。</p>
      </section>

      <section className="fr-panel fr-check" aria-label="稿件核对区域">
        <label id="fr-answer-title" htmlFor="fr-answer">待核对文本</label>
        <textarea id="fr-answer" aria-label="待核对文本" value={answer} onChange={(event) => changeAnswer(event.target.value)} placeholder="填写稿件中需要核对的一段话。" spellCheck={false}/>
        <label className="fr-confirm"><input type="checkbox" checked={confirmed} disabled={locked} onChange={(event) => { retire(); setConfirmed(event.target.checked); }}/><span>我确认原文符合上述 article-lines-v1 行结构。</span></label>
        <div className="fr-run"><button type="submit" className="btn btn-primary" disabled={locked || busy || reading || !confirmed || !source.trim() || !answer.trim()}>{busy ? '正在核对…' : '开始核对'}</button><span className="fr-note">不调用模型 · 不重新评分</span></div>
        {busy && <p className="fr-note" role="status">正在等待本地规则返回结果…</p>}
        {failure && <p className="fr-error" role="alert">{failure}</p>}
        {notice && <p className="fr-note" role="status">{notice}</p>}

        {report && <section className="fr-results" aria-labelledby="fr-results-title">
          <div className="fr-result-heading"><h3 id="fr-results-title">本次核对结果</h3><span className="fr-result-state" data-state={report.status}>{STATUS[report.status]}</span></div>
          {report.diagnostics.map((item, index) => <div className="fr-finding" key={`${item.code}-${index}`}><code>{item.code}</code><p>{item.message}{item.line ? `（原文第${item.line}行）` : ''}</p></div>)}
          {findings.map((item, index) => <article className="fr-finding" data-severity={item.severity} key={`${item.code}-${index}`}><div><span>{SEVERITY[item.severity]}</span><code>{item.code}</code></div><h4>{item.title}</h4><blockquote>{item.text}</blockquote><p>{item.detail}</p></article>)}
          {report.status === 'no_error_found' && <p>当前规则没有检出覆盖范围内的错误。请继续核对主体、条件和所引条款的原文。</p>}
          {report.structureCheck?.status === 'not_provided' && <p className="fr-note">未提供独立款项结构参照，真实款界仍需人工核对。</p>}
          {recoveries.length > 0 && <div className="fr-note">候选回指：{recoveries.map((item) => item.recovered).join('；')}。部分回指不代表完整定位。</div>}
          <div className="fr-downloads"><button type="button" className="btn btn-secondary" onClick={() => download('bundle')}>下载复核包</button><button type="button" className="btn btn-ghost" onClick={() => download('request')}>下载原始请求</button></div>
          <details className="fr-details"><summary>范围、输入绑定与完整输出</summary><ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul><p>原始请求 SHA-256：<code>{report.provenance.inputSha256}</code></p><p>下载原始请求后可复核；SHA-256 只绑定字节。字节一致不证明语义正确或模型输出可复现。</p><pre>Get-FileHash -Algorithm SHA256 -LiteralPath .\finance-review-request.json</pre><pre aria-label="完整规则输出" tabIndex={0}>{JSON.stringify(report, null, 2)}</pre></details>
        </section>}
      </section>
    </form>
    <p className="fr-footnote">数额规则在全部核对资料中查数值与单位，不将同一个数字绑定到特定条款的条件和主体。{selected ? ` ${examples.sourceNote}` : ''}</p>
  </div>;
}
