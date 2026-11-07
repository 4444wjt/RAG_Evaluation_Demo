import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { READ_ONLY, STATIC_SITE } from '../config';
import {
  analyzeTransactions, CLAIM_FIELDS, claimFromCheck, DEFAULT_TRANSACTION_RULES, downloadTransactionBundle,
  parseTransactionInput, readTransactionFile, replayTransactions, resolveTransactionEvidence,
} from '../api/transactionReview';
import type {
  CheckStatus, ClaimField, DecisionValue, EvidenceRef, TransactionBundle, TransactionCheck,
  TransactionClaim, TransactionDecision, TransactionImport, TransactionRequest,
} from '../api/transactionReview';
import example from '../fixtures/transaction-examples.json';
import './transaction-review.css';

const PAGE_SIZE = 25;
const FIELD_LABEL: Record<ClaimField, string> = { amount: '交易金额', customer_id: '交易主体', terminal_id: '交易终端', timestamp: '交易时间', window_count: '窗口笔数', window_total: '窗口合计', fraud_confirmed: '是否已认定欺诈' };
const CHECK_LABEL: Record<CheckStatus, string> = { consistent: '字段一致', inconsistent: '字段不一致', review: '需要复核' };
const DECISION_LABEL: Record<DecisionValue, string> = { pending: '待复核', escalate: '升级调查', dismiss: '排除线索', need_more: '补充证据' };
const REASON_LABEL: Record<string, string> = {
  MATCH: '值与所需引用范围一致', VALUE_MISMATCH: '值与交易记录不一致', EVIDENCE_MISSING: '缺少引用证据',
  EVIDENCE_UNKNOWN: '引用了未知交易 ID', EVIDENCE_INCOMPLETE: '引用未涵盖全部所需交易', EVIDENCE_OUTSIDE_SCOPE: '引用含其他主体、未来或窗口外交易',
  TRANSACTION_UNKNOWN: '说明中的交易 ID 不在输入中', FRAUD_NOT_PROVABLE: '交易记录不能证明欺诈，也不能证明不存在欺诈',
};
type ClaimDraft = { transactionId: string; field: ClaimField; value: string; mode: 'ids' | 'slice'; ids: string; group: string; start: string; end: string };
function toDraft(claim: TransactionClaim): ClaimDraft {
  return { transactionId: claim.transaction_id, field: claim.field, value: String(claim.value), mode: claim.evidence_ref ? 'slice' : 'ids', ids: JSON.stringify(claim.evidence_ids ?? []), group: claim.evidence_ref?.group_id ?? '', start: String(claim.evidence_ref?.start ?? 0), end: String(claim.evidence_ref?.end ?? 0) };
}
function wholeNumber(text: string, label: string): number {
  if (!/^\d+$/.test(text) || !Number.isSafeInteger(Number(text))) throw new Error(`${label}须为非负整数。`);
  return Number(text);
}
function fromDraft(draft: ClaimDraft): TransactionClaim {
  if (!draft.transactionId.trim()) throw new Error('请填写说明交易 ID。');
  const value = draft.field === 'window_count' ? wholeNumber(draft.value, '说明笔数') : draft.field === 'fraud_confirmed' ? draft.value === 'true' : draft.value;
  const claim = { transaction_id: draft.transactionId, field: draft.field, value };
  if (draft.mode === 'slice') return { ...claim, evidence_ref: { group_id: draft.group, start: wholeNumber(draft.start, '切片起点'), end: wholeNumber(draft.end, '切片终点') } };
  let ids: unknown;
  try { ids = JSON.parse(draft.ids); } catch { throw new Error('引用交易 ID 必须是 JSON 文本数组，例如 ["t001","t002"]。'); }
  if (!Array.isArray(ids) || !ids.every((id) => typeof id === 'string')) throw new Error('引用交易 ID 必须是 JSON 文本数组。');
  return { ...claim, evidence_ids: ids };
}
const display = (value: unknown) => value === null ? '交易记录不足以判断' : typeof value === 'boolean' ? value ? '是' : '否' : String(value);
const refText = (ref: EvidenceRef) => `${ref.group_id} [${ref.start}, ${ref.end}) · ${ref.end - ref.start} 笔`;

function Pager({ page, total, size = PAGE_SIZE, label, onChange }: { page: number; total: number; size?: number; label: string; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="tr-pager"><button type="button" className="btn btn-ghost" aria-label={`上一页${label}`} disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</button><label>{label}页码<input type="number" aria-label={`${label}页码`} min={1} max={pages} value={page} onChange={(event) => onChange(Math.min(pages, Math.max(1, Number(event.target.value) || 1)))}/><span>/ {pages}</span></label><button type="button" className="btn btn-ghost" aria-label={`下一页${label}`} disabled={page >= pages} onClick={() => onChange(page + 1)}>下一页</button></div>;
}

export function TransactionReviewView() {
  const locked = READ_ONLY || STATIC_SITE;
  const [source, setSource] = useState('');
  const [draft, setDraft] = useState<TransactionRequest | null>(null);
  const [currency, setCurrency] = useState('CNY');
  const [highAmount, setHighAmount] = useState(DEFAULT_TRANSACTION_RULES.highAmount);
  const [windowMinutes, setWindowMinutes] = useState(String(DEFAULT_TRANSACTION_RULES.windowMinutes));
  const [minTransactions, setMinTransactions] = useState(String(DEFAULT_TRANSACTION_RULES.minTransactions));
  const [claims, setClaims] = useState<ClaimDraft[]>([]);
  const [claimIndex, setClaimIndex] = useState(0);
  const [pendingBundle, setPendingBundle] = useState<Extract<TransactionImport, { kind: 'bundle' }> | null>(null);
  const [current, setCurrent] = useState<TransactionBundle | null>(null);
  const [decisions, setDecisions] = useState<Map<string, TransactionDecision>>(() => new Map());
  const [busy, setBusy] = useState<'read' | 'analyze' | 'replay' | null>(null);
  const [failure, setFailure] = useState('');
  const [notice, setNotice] = useState('');
  const [ignored, setIgnored] = useState<string[]>([]);
  const [sample, setSample] = useState(false);
  const [casePage, setCasePage] = useState(1);
  const [selectedId, setSelectedId] = useState('');
  const [evidencePage, setEvidencePage] = useState(1);
  const [evidenceOverride, setEvidenceOverride] = useState<{ label: string; ids: string[] } | null>(null);
  const [allChecksOpen, setAllChecksOpen] = useState(false);
  const [checkPage, setCheckPage] = useState(1);
  const task = useRef(0);
  const cancellation = useRef<AbortController | null>(null);
  const claimEditor = useRef<HTMLDetailsElement | null>(null);
  const claimIdInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => () => { task.current += 1; cancellation.current?.abort(); }, []);
  const retire = () => {
    task.current += 1; cancellation.current?.abort(); cancellation.current = null;
    setCurrent(null); setDecisions(new Map()); setFailure(''); setNotice(''); setBusy(null);
    setSelectedId(''); setCasePage(1); setEvidencePage(1); setEvidenceOverride(null); setCheckPage(1);
  };
  const applyRequest = (request: TransactionRequest) => {
    setDraft(request); setCurrency(request.currency); setHighAmount(request.rules.highAmount);
    setWindowMinutes(String(request.rules.windowMinutes)); setMinTransactions(String(request.rules.minTransactions));
    setClaims(request.claims.map(toDraft)); setClaimIndex(0);
  };
  const adopt = (parsed: TransactionImport) => {
    if (parsed.kind === 'bundle') {
      setDraft(null); setClaims([]); setPendingBundle(parsed); setIgnored([]);
      setNotice('复核包已载入，尚未验证。点击“重开并验证”后等待本地服务完整重算。');
    } else {
      applyRequest(parsed.request); setPendingBundle(null); setIgnored(parsed.ignoredColumns);
      setNotice(`已读取 ${parsed.request.transactions.length} 笔交易；请核对币种、规则与结构化说明后分析。`);
    }
  };
  const changeSource = (value: string) => {
    if (locked) return;
    retire(); setSource(value); setDraft(null); setPendingBundle(null); setClaims([]); setIgnored([]); setSample(false);
  };
  const readPaste = () => {
    if (locked || !source.trim()) return;
    retire(); setDraft(null); setPendingBundle(null); setClaims([]); setIgnored([]); setSample(false);
    try { adopt(parseTransactionInput(source)); } catch (error) { setFailure(error instanceof Error ? error.message : '输入未能读取。'); }
  };
  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
    if (!file || locked) return;
    retire(); setDraft(null); setPendingBundle(null); setClaims([]); setIgnored([]); setSample(false); setBusy('read');
    const token = task.current; const controller = new AbortController(); cancellation.current = controller;
    try {
      const text = await readTransactionFile(file, controller.signal);
      if (token !== task.current) return;
      setSource(text); adopt(parseTransactionInput(text, /\.csv$/i.test(file.name) ? 'csv' : 'json'));
    } catch (error) { if (token === task.current) setFailure(error instanceof Error ? error.message : '文件未能导入。'); }
    finally { if (token === task.current) { setBusy(null); cancellation.current = null; } }
  };
  const loadExample = () => {
    if (locked) return;
    retire(); const text = JSON.stringify(example.request, null, 2); setSource(text);
    adopt(parseTransactionInput(text)); setSample(true);
  };
  const editSetting = (set: (value: string) => void, value: string) => {
    retire(); setPendingBundle(null); set(value);
  };
  const editClaim = (patch: Partial<ClaimDraft>) => {
    retire(); setPendingBundle(null); setClaims((items) => items.map((item, index) => index === claimIndex ? { ...item, ...patch } : item));
  };
  const addClaim = () => {
    if (!draft || locked) return;
    const row = draft.transactions[0]; retire(); setPendingBundle(null);
    setClaimIndex(claims.length); setClaims([...claims, toDraft({ transaction_id: row.transaction_id, field: 'amount', value: row.amount, evidence_ids: [row.transaction_id] })]);
    if (claimEditor.current) claimEditor.current.open = true;
  };
  const editCheck = (check: TransactionCheck) => {
    const claim = claimFromCheck(check); const next = toDraft(claim);
    const index = claims.findIndex((item) => item.transactionId === claim.transaction_id && item.field === claim.field);
    retire(); setPendingBundle(null);
    if (index >= 0) { setClaims((items) => items.map((item, position) => position === index ? next : item)); setClaimIndex(index); }
    else { setClaimIndex(claims.length); setClaims([...claims, next]); }
    if (claimEditor.current) claimEditor.current.open = true;
    window.requestAnimationFrame(() => claimIdInput.current?.focus());
  };
  const buildRequest = (): TransactionRequest => {
    if (!draft) throw new Error('请先读取交易输入。');
    return { schemaVersion: 1, currency, transactions: draft.transactions, rules: { highAmount, windowMinutes: wholeNumber(windowMinutes, '窗口分钟数'), minTransactions: wholeNumber(minTransactions, '窗口最少笔数') }, claims: claims.map(fromDraft) };
  };
  const accept = (bundle: TransactionBundle) => {
    setCurrent(bundle); setDecisions(new Map(bundle.decisions.map((item) => [item.transaction_id, item])));
    setSelectedId(bundle.report.cases[0]?.id ?? ''); setCasePage(1); setEvidencePage(1); setEvidenceOverride(null); setCheckPage(1);
  };
  const run = async (event: FormEvent) => {
    event.preventDefault(); if (locked || busy || !draft) return;
    retire(); setBusy('analyze'); const token = task.current;
    const controller = new AbortController(); cancellation.current = controller;
    try { const bundle = await analyzeTransactions(buildRequest(), controller.signal); if (token === task.current) accept(bundle); }
    catch (error) { if (token === task.current) setFailure(error instanceof Error ? error.message : '未取得交易复核结果。'); }
    finally { if (token === task.current) { setBusy(null); cancellation.current = null; } }
  };
  const reopen = async () => {
    if (locked || busy || !pendingBundle) return;
    const input = pendingBundle; retire(); setBusy('replay'); const token = task.current;
    const controller = new AbortController(); cancellation.current = controller;
    try {
      const bundle = await replayTransactions(input.bundle, controller.signal, input.sourceText);
      if (token !== task.current) return;
      applyRequest(bundle.request); accept(bundle); setNotice('服务重开验证通过');
    } catch (error) { if (token === task.current) setFailure(error instanceof Error ? error.message : '复核包未能通过重开验证。'); }
    finally { if (token === task.current) { setBusy(null); cancellation.current = null; } }
  };
  const download = () => {
    if (!current) return;
    setFailure(''); setNotice('');
    const completeDecisions = current.report.cases.map((item) => decisions.get(item.id) ?? { transaction_id: item.id, decision: 'pending' as const, reason: '' });
    try { downloadTransactionBundle({ ...current, decisions: completeDecisions }); setNotice('已触发下载，请在浏览器下载列表确认 transaction-review-bundle.json。'); }
    catch (error) { setFailure(error instanceof Error ? error.message : '下载未完成，请检查浏览器设置。'); }
  };

  const report = current?.report;
  const selected = report?.cases.find((item) => item.id === selectedId);
  const sourceRows = useMemo(() => new Map(current?.request.transactions.map((item) => [item.transaction_id, item]) ?? []), [current]);
  const windowIds = useMemo(() => report && selected ? resolveTransactionEvidence(report, selected.window.evidence_ref) : [], [report, selected]);
  const evidenceIds = evidenceOverride?.ids ?? windowIds;
  const activeClaim = claims[claimIndex];
  const decision = selected ? decisions.get(selected.id) ?? { transaction_id: selected.id, decision: 'pending' as const, reason: '' } : null;
  const showEvidence = (check: TransactionCheck, required: boolean) => {
    if (!report) return;
    const ref = required ? check.required_evidence_ref : check.evidence_ref;
    const ids = required ? check.required_evidence_ids : check.evidence_ids;
    setEvidenceOverride({ label: `${FIELD_LABEL[check.field]} · ${required ? '所需证据' : '实际引用'}`, ids: ref ? resolveTransactionEvidence(report, ref) : ids ?? [] }); setEvidencePage(1);
  };
  const fact = (check: TransactionCheck, index: number) => <article className="tr-fact" key={`${check.transaction_id}-${check.field}-${index}`}>
    <div className="tr-fact-heading"><strong>{FIELD_LABEL[check.field]}</strong><span className="tr-status" data-status={check.status}>{CHECK_LABEL[check.status]}</span></div>
    <p className="tr-note"><code>{check.transaction_id}</code> · 说明值 <b>{display(check.value)}</b> · 记录值 <b data-expected>{display(check.expected)}</b></p>
    <p>{check.reasons.map((reason) => REASON_LABEL[reason] ?? reason).join('；')}</p>
    <p className="tr-note">实际引用：{check.evidence_ref ? refText(check.evidence_ref) : `${check.evidence_ids?.length ?? 0} 个交易 ID`}；所需：{check.required_evidence_ref ? refText(check.required_evidence_ref) : check.required_evidence_ids ? `${check.required_evidence_ids.length} 个交易 ID` : '此项不能由交易记录证明'}</p>
    <div className="tr-inline-actions"><button type="button" className="btn btn-ghost" aria-label={`查看引用 ${check.transaction_id} ${check.field}`} onClick={() => showEvidence(check, false)}>查看实际引用</button>{(check.required_evidence_ref || check.required_evidence_ids) && <button type="button" className="btn btn-ghost" aria-label={`查看所需证据 ${check.transaction_id} ${check.field}`} onClick={() => showEvidence(check, true)}>查看所需证据</button>}<button type="button" className="btn btn-secondary" aria-label={`编辑说明 ${check.transaction_id} ${check.field}`} onClick={() => editCheck(check)}>编辑此说明</button></div>
  </article>;

  const evidenceView = report && (selected || evidenceOverride) ? <section className="tr-section" aria-labelledby="tr-evidence-title"><div className="tr-panel-heading"><h4 id="tr-evidence-title">来源交易</h4>{evidenceOverride && <button type="button" className="btn btn-ghost" onClick={() => { setEvidenceOverride(null); setEvidencePage(1); }}>{selected ? '返回案件窗口' : '关闭引用来源'}</button>}</div><p className="tr-note">{evidenceOverride ? `${evidenceOverride.label} · ${evidenceIds.length} 笔` : selected ? `案件窗口引用：${refText(selected.window.evidence_ref)}` : ''}。下表显示原始输入行，保留原始时区和金额文本。</p><div className="tr-table-scroll" tabIndex={0} role="region" aria-label="来源交易表格，可横向滚动"><table aria-label={selected ? "所选案件的来源交易" : "字段检查的来源交易"}><thead><tr><th scope="col">交易 ID</th><th scope="col">时间（原始）</th><th scope="col">主体</th><th scope="col">终端</th><th scope="col">金额 · {report.currency}</th></tr></thead><tbody>{evidenceIds.slice((evidencePage - 1) * PAGE_SIZE, evidencePage * PAGE_SIZE).map((id, index) => { const row = sourceRows.get(id); return <tr key={`${id}-${index}`}><th scope="row">{id}</th>{row ? <><td>{row.timestamp}</td><td>{row.customer_id}</td><td>{row.terminal_id}</td><td>{row.amount}</td></> : <td colSpan={4}>本次原始输入中没有此 ID，缺失字段未填零。</td>}</tr>; })}</tbody></table></div>{!evidenceIds.length && <p className="tr-note">没有提供这项引用的来源行。</p>}{evidenceIds.length > PAGE_SIZE && <Pager page={evidencePage} total={evidenceIds.length} label="来源交易" onChange={setEvidencePage}/>}</section> : null;

  return <div className="tr-view">
    <section className="tr-intro" aria-labelledby="tr-title"><div><p className="tr-eyebrow">交易记录 → 规则线索 → 证据 → 人工复核</p><h2 id="tr-title">逐笔查清线索的依据</h2><p>高金额可能是正常业务，未触发线索不等于安全。字段一致只说明所列值和引用匹配，不证明交易来源真实。</p></div><button type="button" className="btn btn-secondary" disabled={locked} onClick={loadExample}>模拟边界示例</button></section>
    {locked && <p className="tr-error">公开演示和静态页面不接收交易资料；请在本地服务中使用此入口。</p>}
    {sample && <p className="tr-sample"><strong>当前为预置模拟材料。</strong>{example.note}</p>}

    <form className="tr-input-grid" onSubmit={(event) => { void run(event); }}>
      <section className="tr-panel tr-import" aria-labelledby="tr-import-title">
        <div className="tr-panel-heading"><h3 id="tr-import-title">1 · 交易输入</h3><label className={`btn btn-secondary tr-upload${locked ? ' tr-disabled' : ''}`}>导入 CSV / JSON<input type="file" accept=".csv,.json,text/csv,application/json" aria-label="导入交易 CSV / JSON / 复核包" disabled={locked} onChange={(event) => { void importFile(event); }}/></label></div>
        <label htmlFor="tr-source" className="tr-note">CSV / JSON 输入</label>
        <textarea id="tr-source" aria-label="CSV / JSON 输入" aria-describedby="tr-input-help" value={source} disabled={locked} onChange={(event) => changeSource(event.target.value)} spellCheck={false} placeholder="粘贴 CSV、完整交易请求 JSON，或 TransactionReviewBundleV1 复核包。"/>
        <p id="tr-input-help" className="tr-note">UTF-8 · 交易请求 ≤ 2 MiB / 5000 行 · 复核包 ≤ 24 MiB。CSV 必需列：transaction_id、timestamp、customer_id、terminal_id、amount。JSON 仅接收合同字段。时间必须带时区，最多六位微秒；金额不转为浮点数。</p>
        {ignored.length > 0 && <p className="tr-sample">忽略额外列：{ignored.join('、')}。这些列不会进入规则或证据核对。</p>}
        <div className="tr-inline-actions"><button type="button" className="btn btn-secondary" disabled={locked || !source.trim() || busy === 'read'} onClick={readPaste}>读取粘贴内容</button><span className="tr-note">再次读取会替换当前规则与说明。</span></div>
        <div className="tr-submit"><button type="submit" className="btn btn-primary" disabled={locked || !!busy || !draft}>{busy === 'analyze' ? '正在分析…' : '分析并核对'}</button><button type="button" className="btn btn-secondary" disabled={locked || !!busy || !pendingBundle} onClick={() => { void reopen(); }}>{busy === 'replay' ? '正在重开验证…' : '重开并验证'}</button>{busy && <button type="button" className="btn btn-ghost" onClick={() => { retire(); setNotice('已取消当前操作。输入仍保留。'); }}>取消当前操作</button>}</div>
        <p className="tr-note">不调用模型 · 不自动重试 · 仅显式分析/重开发送本地 API · 不自动保存交易</p>
      </section>

      <section className="tr-panel tr-settings" aria-labelledby="tr-settings-title">
        <div className="tr-panel-heading"><h3 id="tr-settings-title">2 · 规则与结构化说明</h3><span className="tr-note">{draft ? `${draft.transactions.length} 笔已读取` : pendingBundle ? '复核包等待服务验证' : '等待交易输入'}</span></div>
        <div className="tr-rule-grid"><label>单一币种<input aria-label="单一币种" value={currency} maxLength={3} disabled={locked || !draft} onChange={(event) => editSetting(setCurrency, event.target.value)}/></label><label>大额阈值<input aria-label="大额阈值" inputMode="decimal" value={highAmount} disabled={locked || !draft} onChange={(event) => editSetting(setHighAmount, event.target.value)}/></label><label>窗口分钟数<input aria-label="窗口分钟数" inputMode="numeric" value={windowMinutes} disabled={locked || !draft} onChange={(event) => editSetting(setWindowMinutes, event.target.value)}/></label><label>窗口最少笔数<input aria-label="窗口最少笔数" inputMode="numeric" value={minTransactions} disabled={locked || !draft} onChange={(event) => editSetting(setMinTransactions, event.target.value)}/></label></div>
        <p className="tr-rule-summary">两条演示规则：金额 ≥ {highAmount || '待填'} {currency}；同主体最近 {windowMinutes || '待填'} 分钟内笔数 ≥ {minTransactions || '待填'}。包含窗口两端与全部同刻交易，排除未来和其他主体。</p>
        <details className="tr-claim-editor" ref={claimEditor}><summary>结构化说明 · {claims.length} 条</summary><p className="tr-note">仅核对下列字段，不声称核验普通自由文本。留空时，服务为线索案件生成固定事实；填写后仅检查所列说明；输入、规则或说明改变都会撤下旧结果和人工决定。</p>
          <div className="tr-inline-actions"><button type="button" className="btn btn-secondary" disabled={locked || !draft} onClick={addClaim}>添加说明</button>{activeClaim && <button type="button" className="btn btn-ghost" disabled={locked} onClick={() => { retire(); setPendingBundle(null); setClaims((items) => items.filter((_, index) => index !== claimIndex)); setClaimIndex(Math.max(0, claimIndex - 1)); }}>删除当前说明</button>}</div>
          {activeClaim && <div className="tr-claim-fields"><div className="tr-wide tr-claim-navigation"><label>说明序号<input type="number" aria-label="说明序号" min={1} max={claims.length} value={claimIndex + 1} onChange={(event) => setClaimIndex(Math.min(claims.length - 1, Math.max(0, (Number(event.target.value) || 1) - 1)))}/></label><span className="tr-note">/ {claims.length} 条</span><button type="button" className="btn btn-ghost" aria-label="上一条说明" disabled={claimIndex <= 0} onClick={() => setClaimIndex(claimIndex - 1)}>上一条</button><button type="button" className="btn btn-ghost" aria-label="下一条说明" disabled={claimIndex >= claims.length - 1} onClick={() => setClaimIndex(claimIndex + 1)}>下一条</button></div><label>说明交易 ID<input ref={claimIdInput} aria-label="说明交易 ID" value={activeClaim.transactionId} onChange={(event) => editClaim({ transactionId: event.target.value })}/></label><label>说明字段<select aria-label="说明字段" value={activeClaim.field} onChange={(event) => { const field = event.target.value as ClaimField; editClaim({ field, ...(field === 'fraud_confirmed' ? { value: 'true' } : {}) }); }}>{CLAIM_FIELDS.map((field) => <option key={field} value={field}>{FIELD_LABEL[field]} · {field}</option>)}</select></label><label className="tr-wide">说明值{activeClaim.field === 'fraud_confirmed' ? <select aria-label="说明值" value={activeClaim.value} onChange={(event) => editClaim({ value: event.target.value })}><option value="true">是（交易记录不能证明）</option><option value="false">否（交易记录不能证明）</option></select> : <input aria-label="说明值" value={activeClaim.value} onChange={(event) => editClaim({ value: event.target.value })}/>}</label><label className="tr-wide">引用方式<select aria-label="引用方式" value={activeClaim.mode} onChange={(event) => editClaim({ mode: event.target.value as ClaimDraft['mode'] })}><option value="ids">交易 ID 列表</option><option value="slice">当前报告索引切片</option></select></label>{activeClaim.mode === 'ids' ? <label className="tr-wide">引用交易 ID（JSON 数组）<textarea aria-label="引用交易 ID（JSON 数组）" value={activeClaim.ids} onChange={(event) => editClaim({ ids: event.target.value })} spellCheck={false}/><span className="tr-note">例如 ["t001","t002"]；[] 表示未提供引用，交易 ID 逐字保留。</span></label> : <><label className="tr-wide">证据组 ID<input aria-label="证据组 ID" value={activeClaim.group} onChange={(event) => editClaim({ group: event.target.value })}/></label><label>切片起点<input aria-label="切片起点" inputMode="numeric" value={activeClaim.start} onChange={(event) => editClaim({ start: event.target.value })}/></label><label>切片终点（不含）<input aria-label="切片终点（不含）" inputMode="numeric" value={activeClaim.end} onChange={(event) => editClaim({ end: event.target.value })}/></label><p className="tr-note tr-wide">零基 [起点, 终点)；证据组仅属于当前输入，更换输入后须重新核对。</p></>}</div>}
        </details>
      </section>
    </form>
    <div className="tr-messages" aria-live="polite">{busy && <p className="tr-note" role="status">{busy === 'read' ? '正在读取文件…' : busy === 'replay' ? '本地服务正在完整重算复核包；上传报告尚未采信。' : '正在等待本地交易规则与字段检查…'}</p>}{failure && <p className="tr-error" role="alert">{failure}</p>}{notice && <p className="tr-note" role="status">{notice}</p>}</div>

    {report && current && <section className="tr-results" aria-labelledby="tr-results-title">
      <div className="tr-result-heading"><div><h3 id="tr-results-title">本次交易复核结果</h3><p className="tr-note">总计 {report.summary.totalTransactions} 笔 · 线索 {report.summary.flaggedTransactions} 笔</p></div><span className="tr-status" data-status={report.status === 'review' ? 'review' : 'consistent'}>{report.status === 'review' ? '仍需复核' : '规则未触发线索'}</span><button type="button" className="btn btn-secondary" onClick={download}>下载完整复核包</button></div>
      <dl className="tr-metrics"><div><dt>交易总数</dt><dd>{report.summary.totalTransactions}</dd></div><div><dt>规则线索</dt><dd>{report.summary.flaggedTransactions}</dd></div><div><dt>字段一致</dt><dd>{report.summary.consistentClaims}</dd></div><div><dt>字段不一致</dt><dd>{report.summary.inconsistentClaims}</dd></div><div><dt>字段待复核</dt><dd>{report.summary.reviewClaims}</dd></div></dl>
      <div className="tr-case-grid">
        <section className="tr-panel tr-cases" aria-labelledby="tr-cases-title"><div className="tr-panel-heading"><h3 id="tr-cases-title">风险线索案件</h3><span className="tr-note">每页 {PAGE_SIZE} 条</span></div>{!report.cases.length && <p className="tr-note">本次没有产生规则线索案件。字段检查仍在下方保留；可更改输入后重新分析。</p>}<div className="tr-case-list">{report.cases.slice((casePage - 1) * PAGE_SIZE, casePage * PAGE_SIZE).map((item) => <button type="button" key={item.id} className="tr-case-button" aria-label={`选择案件 ${item.id}`} aria-pressed={selectedId === item.id} onClick={() => { setSelectedId(item.id); setEvidenceOverride(null); setEvidencePage(1); }}><span><strong>{item.id}</strong><small>{item.transaction.customer_id}</small></span><span><b>{item.transaction.amount} {report.currency}</b><small>{CHECK_LABEL[item.evidenceStatus]} · {DECISION_LABEL[decisions.get(item.id)?.decision ?? 'pending']}</small></span></button>)}</div>{report.cases.length > 0 && <Pager page={casePage} total={report.cases.length} label="案件" onChange={(page) => { setCasePage(page); setSelectedId(report.cases[(page - 1) * PAGE_SIZE]?.id ?? ''); setEvidenceOverride(null); setEvidencePage(1); }}/>}</section>
        {selected && decision ? <section className="tr-panel tr-case-detail" aria-label={`案件 ${selected.id} 详情`}><div className="tr-panel-heading"><h3>案件 {selected.id}</h3><span className="tr-note">{selected.transaction.timestamp}</span></div>
          <dl className="tr-case-meta"><div><dt>主体</dt><dd>{selected.transaction.customer_id}</dd></div><div><dt>终端</dt><dd>{selected.transaction.terminal_id}</dd></div><div><dt>金额 · {report.currency}</dt><dd>{selected.transaction.amount}</dd></div></dl>
          <section className="tr-section" aria-labelledby="tr-signals-title"><h4 id="tr-signals-title">规则线索</h4>{selected.signals.map((signal) => <p className="tr-signal" key={signal.rule}>{signal.rule === 'high_amount' ? `大额：${signal.value} ${report.currency} ≥ ${signal.threshold} ${report.currency}` : `同主体 ${signal.windowMinutes} 分钟窗口：${signal.value} 笔 ≥ ${signal.threshold} 笔`}</p>)}<p className="tr-note">窗口 [{selected.window.start}, {selected.window.end}] · {selected.window.count} 笔 · 合计 {selected.window.total} {report.currency}</p></section>
          <section className="tr-section" aria-labelledby="tr-facts-title"><div className="tr-panel-heading"><h4 id="tr-facts-title">字段事实核对</h4><span className="tr-status" data-status={selected.evidenceStatus}>{CHECK_LABEL[selected.evidenceStatus]}</span></div>{selected.claims.length ? selected.claims.map(fact) : <p className="tr-note">当前显式说明中没有本案字段，不将其视为已核验；可添加结构化说明后重查。</p>}</section>
          {evidenceView}
          <section className="tr-section tr-human"><h4 id="tr-human-title">人工复核决定</h4><p className="tr-note">人工记录不会改变规则线索或字段检查，不触发封号、扣款或转账。复核包不验证决定人的身份，也不检测合法决定被合法改写。</p><label>人工复核决定<select aria-label="人工复核决定" value={decision.decision} onChange={(event) => { setFailure(''); setNotice(''); setDecisions((items) => new Map(items).set(selected.id, { ...decision, decision: event.target.value as DecisionValue })); }}>{Object.entries(DECISION_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>决定原因{decision.decision !== 'pending' && <span className="tr-note"> · 必填</span>}<textarea aria-label="决定原因" value={decision.reason} aria-required={decision.decision !== 'pending'} aria-invalid={decision.decision !== 'pending' && !decision.reason.trim()} onChange={(event) => { setFailure(''); setNotice(''); setDecisions((items) => new Map(items).set(selected.id, { ...decision, reason: event.target.value })); }} placeholder="记录业务解释、需要补充的证据或调查理由。"/></label></section>
        </section> : <section className="tr-panel">{evidenceView ?? <><h3>案件详情</h3><p className="tr-note">本次没有可选择的线索案件。全部字段检查仍可独立查看。</p></>}</section>}
      </div>
      <details className="tr-panel tr-all-checks" onToggle={(event) => setAllChecksOpen(event.currentTarget.open)}><summary>全部字段检查 · {report.claimChecks.length} 条（含未产生案件的交易）</summary>{allChecksOpen && <><p className="tr-note">这里与案件详情引用同一批检查，不另加统计分母；每页 10 条。</p><div className="tr-check-grid">{report.claimChecks.slice((checkPage - 1) * 10, checkPage * 10).map(fact)}</div><Pager page={checkPage} total={report.claimChecks.length} size={10} label="字段检查" onChange={setCheckPage}/></>}</details>
      <details className="tr-panel tr-binding"><summary>范围、输入绑定与代码版本</summary><ul>{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul><p>引擎：<code>{report.engineVersion}</code></p><p>规范化请求 SHA-256：<code>{report.provenance.requestSha256}</code></p><p>该值绑定服务端规范化请求：显式默认值、UTC 时间、十进制金额字符串、按时间/ID 排序的交易及排序键紧凑 UTF-8 JSON。它不是下载包文件的字节哈希；输入来源真实性、语义正确性和模型可复现性不由哈希证明。</p><ul>{Object.entries(report.provenance.codeSha256).map(([path, digest]) => <li key={path}><code>{path}</code><br/><code>{digest}</code></li>)}</ul><p>重开由本地服务重算整个报告并检查输入/代码绑定及决定结构；没有数字签名或权威副本，不提供人工决定的身份认证。完整报告仅保存在下载包中，页面按案件和字段分页阅读。</p></details>
    </section>}
  </div>;
}
