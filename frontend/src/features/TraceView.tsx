import { useState } from 'react';
import type { EvaluationResult, JsonValue } from '../api/types';
import { Icon } from '../components/Icon';
import { modelName, textValue } from '../utils/format';
import './details.css';

type Verdict = 'supported' | 'fact_error' | 'fabrication' | 'unknown';
type Statement = { text: string; verdict: Verdict; evidence: string; originalVerdict: string };
type Context = { text: string; originalText: boolean };
type Selection = { result: EvaluationResult | null; model: string; index: number };
type AnswerMode = 'normal' | 'refusal' | 'refusal_explanation' | 'premise_correction' | 'unknown';
type TraceData = { statements: Statement[] | null; status: string | null; attempts: number | null; answerMode: AnswerMode | null; diagnostics?: Record<string, unknown> };
type Tone = 'positive' | 'info' | 'warning' | 'danger' | 'neutral';
type TraceState = 'evidence_ready' | 'refusal' | 'unexpected_refusal' | 'no_trace' | 'filtered_all' | 'trace_error' | 'legacy' | 'unsupported';
type Direction = '利好' | '待复核' | '链路异常' | '模型风险' | '无法评估';

const verdictLabels: Record<Verdict, string> = {
  supported: '有依据',
  fact_error: '事实错误',
  fabrication: '无依据编造',
  unknown: '未知判定',
};

const answerModeCopy: Record<AnswerMode, { label: string; description: string; tone: Tone }> = {
  normal: { label: '正常回答', description: '模型对问题给出了事实性回答。', tone: 'positive' },
  refusal: { label: '明确拒答', description: '模型明确表示当前材料不足以回答，未编造无依据事实。', tone: 'info' },
  refusal_explanation: { label: '拒答说明', description: '模型拒绝回答，并附带原因说明。', tone: 'info' },
  premise_correction: { label: '前提纠正', description: '模型指出问题前提不成立，并给出纠正说明。', tone: 'positive' },
  unknown: { label: '回答类型未记录', description: '历史数据缺少回答类型字段，无法确认模型回答属于何种类型。', tone: 'neutral' },
};

function answerModeValue(value: unknown): AnswerMode | null {
  return value === 'normal' || value === 'refusal' || value === 'refusal_explanation'
    || value === 'premise_correction' || value === 'unknown' ? value : null;
}

function readTrace(value: JsonValue | undefined): TraceData {
  if (value === null || value === undefined || value === '') return { statements: null, status: 'missing', attempts: null, answerMode: null };
  let trace: unknown = value;
  if (typeof trace === 'string') {
    try { trace = JSON.parse(trace); } catch { return { statements: null, status: 'parse_error', attempts: null, answerMode: null }; }
  }
  if (!trace || typeof trace !== 'object' || Array.isArray(trace)) return { statements: null, status: 'invalid_structure', attempts: null, answerMode: null };
  const traceObject = trace as Record<string, unknown>;
  const diagnostics = traceObject.trace_diagnostics && typeof traceObject.trace_diagnostics === 'object' && !Array.isArray(traceObject.trace_diagnostics)
    ? traceObject.trace_diagnostics as Record<string, unknown> : undefined;
  const statements: unknown = traceObject.statements;
  const recordedStatus = typeof traceObject.trace_status === 'string' ? traceObject.trace_status : null;
  const attempts = typeof traceObject.trace_attempts === 'number' && Number.isFinite(traceObject.trace_attempts)
    ? traceObject.trace_attempts : null;
  const answerMode = answerModeValue(diagnostics?.answer_mode);
  if (!Array.isArray(statements)) return { statements: null, status: recordedStatus ?? 'invalid_structure', attempts, answerMode, diagnostics };
  const parsed: Statement[] = [];
  for (const item of statements) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return { statements: null, status: recordedStatus ?? 'invalid_structure', attempts, answerMode, diagnostics };
    const statement = item as Record<string, unknown>;
    if (typeof statement.text !== 'string' || !statement.text.trim()) return { statements: null, status: recordedStatus ?? 'invalid_structure', attempts, answerMode, diagnostics };
    const originalVerdict = typeof statement.verdict === 'string' ? statement.verdict : '';
    const verdict: Verdict = originalVerdict === 'supported' || originalVerdict === 'fact_error' || originalVerdict === 'fabrication'
      ? originalVerdict : 'unknown';
    parsed.push({
      text: statement.text,
      verdict,
      originalVerdict,
      evidence: typeof statement.evidence === 'string' ? statement.evidence : '',
    });
  }
  const status = recordedStatus ?? (parsed.length === 0 ? 'legacy_empty' : 'legacy_nonempty');
  return { statements: parsed, status, attempts, answerMode, diagnostics };
}

const traceStateCopy: Record<TraceState, { label: string; description: string; tone: Tone }> = {
  evidence_ready: { label: '证据链已生成', description: '回答中的事实陈述已生成逐句判定，并关联检索依据。', tone: 'positive' },
  refusal: { label: '无事实陈述需验证', description: '模型拒答未产生需要验证的事实陈述，因此没有逐句证据链。', tone: 'info' },
  unexpected_refusal: { label: '异常拒答', description: '该正向用例应可依据材料回答，但模型选择了拒答，需要检查。', tone: 'warning' },
  no_trace: { label: '证据链未生成', description: '模型已生成回答，但当前记录无法建立回答与检索依据的逐句对应关系。', tone: 'warning' },
  filtered_all: { label: '证据链未生成（候选未通过校验）', description: '模型返回了候选陈述，但全部未通过原文校验而被过滤，无法建立回答与检索依据的逐句对应关系。', tone: 'warning' },
  trace_error: { label: 'Trace生成异常', description: 'Trace处理过程发生异常，未完成证据链分析。', tone: 'danger' },
  legacy: { label: '证据链信息未保留', description: '该条记录未保存回答类型与逐句证据链，无法建立回答与检索依据的对应关系。', tone: 'neutral' },
  unsupported: { label: '存在无依据陈述', description: '部分回答陈述未在当前检索上下文中找到支持。', tone: 'danger' },
};

const reliabilityCopy: Record<TraceState, { label: string; description: string; tone: Tone; icon: 'shield' | 'file' | 'alert' | 'trace'; direction: Direction }> = {
  evidence_ready: { label: '可验证', description: '回答中的事实陈述已生成逐句证据关系，可以检查回答是否被上下文支持。', tone: 'positive', icon: 'shield', direction: '利好' },
  refusal: { label: '正确拒答', description: '模型正确识别到检索材料未提供相关信息，选择拒答而非编造，避免了幻觉。', tone: 'info', icon: 'file', direction: '利好' },
  unexpected_refusal: { label: '异常拒答', description: '模型在该正向用例上未给出应有回答，可能漏答；需检查检索或生成环节。', tone: 'warning', icon: 'alert', direction: '待复核' },
  no_trace: { label: '需要检查', description: '模型已生成回答，但当前无法建立回答与检索依据的逐句对应关系。该状态不代表回答一定错误。', tone: 'warning', icon: 'alert', direction: '待复核' },
  filtered_all: { label: '需要检查', description: '模型返回了候选陈述，但全部未通过原文校验而被过滤。需要检查过滤规则是否误删了正常陈述。', tone: 'warning', icon: 'alert', direction: '待复核' },
  trace_error: { label: '评测链路异常', description: 'Trace处理过程失败，无法完成证据验证。', tone: 'danger', icon: 'alert', direction: '链路异常' },
  legacy: { label: '信息不足', description: '该条记录缺少回答类型与逐句证据链信息，无法评估该回答是否有依据；这属于记录本身的信息边界，与本次评测链路无关。', tone: 'neutral', icon: 'trace', direction: '无法评估' },
  unsupported: { label: '存在风险', description: '部分回答陈述未在当前检索上下文中找到支持。', tone: 'danger', icon: 'alert', direction: '模型风险' },
};

function resolveTraceState(trace: TraceData, answerMode: AnswerMode, statements: Statement[] | null, caseType: JsonValue | undefined): TraceState {
  const hasStatements = Boolean(statements && statements.length > 0);

  // 优先级 1：Trace 生成异常（调用失败 / JSON 解析失败 / 结构无效）
  if (trace.status === 'call_error' || trace.status === 'parse_error' || trace.status === 'invalid_structure') {
    return 'trace_error';
  }

  // 优先级 2：有 Trace（statements 存在）
  if (hasStatements) {
    const hasUnsupported = statements!.some((statement) => statement.verdict === 'fabrication' || statement.verdict === 'fact_error');
    return hasUnsupported ? 'unsupported' : 'evidence_ready';
  }

  // 优先级 3：拒答类型
  if (answerMode === 'refusal' || answerMode === 'refusal_explanation') return caseType === 'negative' ? 'refusal' : 'unexpected_refusal';

  // 优先级 3b：候选陈述全部被过滤（区别于模型返回空数组）
  if (trace.status === 'filtered_all') return 'filtered_all';

  // 优先级 4：有回答但无 Trace（模型产生事实性回答）
  if (answerMode === 'normal' || answerMode === 'premise_correction') return 'no_trace';

  // 优先级 4b：有 trace_status 诊断字段但 answer_mode 未记录
  if (trace.status && trace.status !== 'missing' && trace.status !== 'legacy_empty' && trace.status !== 'legacy_nonempty') {
    return 'no_trace';
  }

  // 优先级 5：历史未知（无 trace_status、无 answer_mode）
  return 'legacy';
}

function emptyTraceMessage(status: string | null): { title: string; body: string } {
  switch (status) {
    case 'empty_answer': return { title: '该回答未产生需要验证的事实陈述', body: '该回答是纯拒答，未产生需要验证的事实陈述。' };
    case 'model_empty': return { title: '回答已生成，但逐句证据链未生成', body: '回答已生成，但逐句证据链未生成，无法建立回答与检索依据的对应关系。' };
    case 'filtered_all': return { title: '回答已生成，但逐句证据链未生成', body: '模型返回了候选陈述，但结构或引用未通过原文校验，无法建立回答与检索依据的对应关系。' };
    case 'parse_error': return { title: 'Trace生成异常', body: 'Trace生成异常，请检查原始记录。' };
    case 'invalid_structure': return { title: 'Trace生成异常', body: 'Trace 返回内容缺少有效的 statements 列表，请检查原始记录。' };
    case 'call_error': return { title: 'Trace生成异常', body: '回答已生成，但逐句证据链未生成，无法建立回答与检索依据的对应关系。调用阶段未完成。' };
    case 'disabled': return { title: '证据链未生成', body: '本次评测未执行逐句溯源。' };
    case 'legacy_empty': return { title: '证据链信息未保留', body: '该条记录只保存了空陈述列表，未保存生成、解析或过滤阶段；无法据此判断回答是否有依据。' };
    case 'missing': return { title: '证据链信息未保留', body: '该条记录未保存逐句证据链，无法据此判断回答是否有依据。' };
    default: return { title: '证据链信息未保留', body: '该条记录没有可读取的逐句判定，处理原因未保留；无法据此判断幻觉情况。' };
  }
}

const rejectionLabels: Record<string, string> = {
  invalid_statement: '陈述结构异常', missing_text: '缺少陈述文本', invalid_verdict: '判定值不合法',
  missing_evidence_field: '缺少引用字段', evidence_not_in_context: '引用不在原检索上下文中',
  missing_required_evidence: '该判定缺少必要引用',
};

function TraceDiagnostics({ trace }: { trace: TraceData }) {
  if (!trace.diagnostics) return null;
  const data = trace.diagnostics;
  const rejected = Array.isArray(data.rejected_statements) ? data.rejected_statements : [];
  return <details className="trace-processing-record">
    <summary>Trace 处理记录{trace.status === 'partial' && ' · 部分候选未通过校验'}</summary>
    <div>
      <p>这里只记录调用与格式校验；引用能定位不等于语义判定已经独立复核。</p>
      <dl>
        <div><dt>处理版本</dt><dd>{typeof data.version === 'string' ? data.version : '未记录'}</dd></div>
        <div><dt>响应模型</dt><dd>{typeof data.returned_model === 'string' ? data.returned_model : '未记录'}</dd></div>
        <div><dt>候选陈述</dt><dd>{typeof data.input_statement_count === 'number' ? data.input_statement_count : '未取得'}</dd></div>
        <div><dt>提示摘要</dt><dd>{typeof data.prompt_sha256 === 'string' ? data.prompt_sha256 : '未记录'}</dd></div>
      </dl>
      {rejected.length > 0 && <ul>{rejected.map((item, index) => {
        const rejection = item && typeof item === 'object' ? item as Record<string, unknown> : {};
        const candidate = typeof rejection.index === 'number' ? rejection.index + 1 : '?';
        const reason = typeof rejection.reason === 'string' ? rejectionLabels[rejection.reason] ?? '未知校验结果' : '未知校验结果';
        return <li key={index}>候选 {candidate}：{reason}</li>;
      })}</ul>}
      {typeof data.raw_output === 'string' && <div><h5>模型原始输出</h5><pre>{data.raw_output || '返回内容为空'}</pre></div>}
    </div>
  </details>;
}

function readContexts(value: JsonValue | undefined): Context[] {
  if (typeof value === 'string' && value.trim()) return [{ text: value, originalText: true }];
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
      .map((text) => ({ text, originalText: false }));
  }
  return [];
}

function caseLabel(value: JsonValue | undefined): string {
  if (value === 'positive') return '正向用例';
  if (value === 'negative') return '负向用例';
  return textValue(value, '类型未记录');
}

export function TraceView({ result }: { result: EvaluationResult | null }) {
  const [selection, setSelection] = useState<Selection>({ result: null, model: '', index: 0 });
  const models = result ? Object.keys(result.model_dfs) : [];
  const model = selection.result === result && models.includes(selection.model) ? selection.model : (models[0] ?? '');
  const rows = result?.model_dfs[model] ?? [];
  const selectedIndex = selection.result === result && selection.model === model && selection.index < rows.length ? selection.index : 0;
  const row = rows[selectedIndex];
  const trace = row ? readTrace(row.hallucination_trace) : { statements: null, status: null, attempts: null, answerMode: null };
  const statements = trace.statements;
  const contexts = row ? readContexts(row.contexts) : [];
  const answerMode = trace.answerMode ?? 'unknown';
  const traceState = resolveTraceState(trace, answerMode, statements, row?.case_type);
  const traceStateValue = traceStateCopy[traceState];
  const reliabilityValue = reliabilityCopy[traceState];

  return <section className="trace-view" aria-labelledby="trace-view-title">
    <header className="trace-toolbar">
      <div>
        <h2 id="trace-view-title">逐句溯源</h2>
        <p className="muted">按模型与测试问题检查原始判定、证据和检索上下文。</p>
      </div>
      {models.length > 0 && <label className="trace-model-field">
        <span>检查模型</span>
        <select className="field trace-model-select" value={model} onChange={(event) => setSelection({ result, model: event.target.value, index: 0 })}>
          {models.map((name) => <option key={name} value={name}>{modelName(name)}</option>)}
        </select>
      </label>}
    </header>

    {!result || models.length === 0 ? <div className="empty-state trace-empty">
      <span className="details-empty-icon"><Icon name="trace" size={27} /></span>
      <h3>暂无用例结果</h3>
      <p>载入演示快照或完成评测后，可在这里检查每个问题的回答依据。</p>
    </div> : <div className="trace-workbench">
      <aside className="trace-cases" aria-label="测试问题">
        <div className="trace-cases-heading">
          <h3>测试问题</h3><span className="trace-count">{rows.length} 条</span>
        </div>
        <div className="trace-case-list">
          {rows.map((item, index) => <button
            type="button"
            key={index}
            className={`trace-case${selectedIndex === index ? ' is-selected' : ''}`}
            aria-pressed={selectedIndex === index}
            onClick={() => setSelection({ result, model, index })}
          >
            <span className="trace-case-top"><span className="trace-case-number">问题 {String(index + 1).padStart(2, '0')}</span><span className="trace-case-type">{caseLabel(item.case_type)}</span></span>
            <span className="trace-case-question">{textValue(item.question, '问题原文未记录')}</span>
            <Icon name="chevron" size={15} className="trace-case-chevron" />
          </button>)}
          {rows.length === 0 && <p className="trace-inline-empty">当前模型没有用例记录。</p>}
        </div>
        <div className="trace-case-footer"><Icon name="trace" size={15} /><span>展示已有记录，不重算判定</span></div>
      </aside>

      <div className="trace-detail" key={`${model}-${selectedIndex}`}>
        {row ? <>
          <header className="trace-detail-heading">
            <div className="trace-detail-meta"><span>问题 {String(selectedIndex + 1).padStart(2, '0')}</span><span className="badge trace-type-badge">{caseLabel(row.case_type)}</span></div>
            <h3>{textValue(row.question, '问题原文未记录')}</h3>
            <p className="trace-model-id">{model}</p>
          </header>

          <section className="trace-answer-section" aria-labelledby="trace-answer-title">
            <div className={`trace-reliability-card trace-reliability-card--${reliabilityValue.tone}`} role="status" aria-label={`可靠性判断：${reliabilityValue.label}`}>
              <span className="trace-reliability-icon"><Icon name={reliabilityValue.icon} size={21} /></span>
              <div className="trace-reliability-content">
                <div className="trace-status-stack">
                  <div className="trace-status-level trace-status-level--minor">
                    <span className="trace-status-level-label">回答状态</span>
                    <strong className={`trace-status-level-value trace-status-level-value--${answerModeCopy[answerMode].tone}`}>{answerModeCopy[answerMode].label}</strong>
                    <p className="trace-status-level-desc">{answerModeCopy[answerMode].description}</p>
                  </div>
                  <div className="trace-status-level trace-status-level--minor">
                    <span className="trace-status-level-label">证据链状态</span>
                    <strong className={`trace-status-level-value trace-status-level-value--${traceStateValue.tone}`}>{traceStateValue.label}</strong>
                    <p className="trace-status-level-desc">{traceStateValue.description}</p>
                  </div>
                  <div className="trace-status-level trace-status-level--reliability">
                    <span className="trace-status-level-label">可靠性判断</span>
                    <div className="trace-verdict-line">
                      <strong className={`trace-verdict-value trace-status-level-value--${reliabilityValue.tone}`}>{reliabilityValue.label}</strong>
                      <span className="trace-verdict-direction">{reliabilityValue.direction}</span>
                    </div>
                    <p className="trace-status-level-desc">{reliabilityValue.description}</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="trace-answer-heading">
              <h4 id="trace-answer-title">模型回答</h4>
            </div>
            <p className="trace-answer">{textValue(row.answer, '回答原文未记录')}</p>
            {answerMode === 'unknown' && <p className="trace-answer-note">历史记录没有可靠的回答类型元数据，不能据此判断是正常回答、拒答说明还是前提纠正。</p>}
            {answerMode === 'refusal_explanation' && <p className="trace-answer-note">这是拒答结果；其中的原因说明不等同于普通业务事实，逐句判定仍需结合原始上下文阅读。</p>}
            {typeof row.ground_truth === 'string' && row.ground_truth.trim() && <div className="trace-reference"><span>参考答案</span><p>{row.ground_truth}</p></div>}
          </section>

          <section className="trace-statements-section" aria-labelledby="trace-statements-title">
            <div className="trace-section-heading"><div><h4 id="trace-statements-title">逐句判定</h4><p className="trace-verdict-note">机器逐句判定，不代表人工认证；引用已定位也不等于语义支持。</p></div><span>来自原始溯源记录</span></div>
            {statements && statements.length > 0 ? <div className="trace-statements">
              {statements.map((statement, index) => {
                const matchingContext = statement.evidence.trim()
                  ? contexts.findIndex((context) => context.text.includes(statement.evidence)) : -1;
                const evidenceState = statement.evidence.trim() ? (matchingContext >= 0 ? '引用已定位' : '引用未匹配') : '无引用';
                return <article className={`trace-statement trace-statement--${statement.verdict}`} key={index}>
                  <div className="trace-statement-heading"><span>陈述 {index + 1}</span><div className="trace-statement-badges"><span className={`badge trace-verdict trace-verdict--${statement.verdict}`}>{verdictLabels[statement.verdict]}</span><span className={`trace-evidence-status trace-evidence-status--${evidenceState === '引用已定位' ? 'matched' : evidenceState === '引用未匹配' ? 'unmatched' : 'empty'}`}>{evidenceState}</span></div></div>
                  <p className="trace-statement-text">{statement.text}</p>
                  {statement.verdict === 'unknown' && statement.originalVerdict && <p className="trace-unknown-verdict">原始判定值：{statement.originalVerdict}</p>}
                  <div className="trace-evidence">
                    <div className="trace-evidence-label"><Icon name="file" size={14} /><span>原始判定证据</span></div>
                    {statement.evidence.trim() ? <blockquote>{statement.evidence}</blockquote> : <p className="trace-inline-empty">原始记录未提供证据。</p>}
                    {matchingContext >= 0 && <p className="trace-evidence-match"><Icon name="check" size={13} /><span>{contexts[matchingContext].originalText ? '精确匹配检索上下文原文' : `精确匹配检索片段 ${matchingContext + 1}`}</span></p>}
                    {statement.evidence.trim() && matchingContext < 0 && <p className="trace-evidence-unmatched">未在当前检索上下文中找到精确匹配。</p>}
                  </div>
                </article>;
              })}
            </div> : <div className="trace-no-statements"><Icon name="trace" size={22} /><div><h5>{emptyTraceMessage(trace.status).title}</h5><p>{emptyTraceMessage(trace.status).body}</p>{trace.attempts !== null && trace.attempts > 0 && <small>Trace 尝试 {trace.attempts} 次</small>}</div></div>}
            <TraceDiagnostics trace={trace} />
          </section>

          <section className="trace-contexts-section" aria-labelledby="trace-contexts-title">
            <div className="trace-section-heading"><h4 id="trace-contexts-title">检索上下文</h4><span>{contexts.some((context) => context.originalText) ? '保留原始文本' : `${contexts.length} 个片段`}</span></div>
            {contexts.length > 0 ? <div className="trace-contexts">{contexts.map((context, index) => <details className="trace-context" key={index}>
              <summary><Icon name="chevron" size={14} /><span className="trace-context-title">{context.originalText ? '上下文原始文本' : `检索片段 ${index + 1}`}</span><span className="trace-context-preview">{context.text.slice(0, 64)}</span></summary>
              <div className="trace-context-body">{context.originalText && <p className="trace-context-note">文件中保存的上下文原文，未拆分为独立片段。</p>}<p>{context.text}</p></div>
            </details>)}</div> : <p className="trace-inline-empty">当前记录未提供可读取的检索上下文。</p>}
          </section>
        </> : <div className="empty-state trace-detail-empty"><Icon name="trace" size={28} /><h3>暂无用例详情</h3><p>选择有用例记录的模型后查看详情。</p></div>}
      </div>
    </div>}
  </section>;
}
