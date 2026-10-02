import { useState } from 'react';
import type { EvaluationResult, JsonValue } from '../api/types';
import { Icon } from '../components/Icon';
import { modelName, textValue } from '../utils/format';
import './details.css';

type Verdict = 'supported' | 'fact_error' | 'fabrication' | 'unknown';
type Statement = { text: string; verdict: Verdict; evidence: string; originalVerdict: string };
type Context = { text: string; originalText: boolean };
type Selection = { result: EvaluationResult | null; model: string; index: number };

const verdictLabels: Record<Verdict, string> = {
  supported: '有依据',
  fact_error: '事实错误',
  fabrication: '无依据编造',
  unknown: '未知判定',
};

function readStatements(value: JsonValue | undefined): Statement[] | null {
  let trace: unknown = value;
  if (typeof trace === 'string') {
    try { trace = JSON.parse(trace); } catch { return null; }
  }
  if (!trace || typeof trace !== 'object' || Array.isArray(trace)) return null;
  const statements: unknown = (trace as Record<string, unknown>).statements;
  if (!Array.isArray(statements)) return null;
  const parsed: Statement[] = [];
  for (const item of statements) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
    const statement = item as Record<string, unknown>;
    if (typeof statement.text !== 'string' || !statement.text.trim()) return null;
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
  return parsed;
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
  const statements = row ? readStatements(row.hallucination_trace) : null;
  const contexts = row ? readContexts(row.contexts) : [];

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
            <h4 id="trace-answer-title">模型回答</h4>
            <p className="trace-answer">{textValue(row.answer, '回答原文未记录')}</p>
            {typeof row.ground_truth === 'string' && row.ground_truth.trim() && <div className="trace-reference"><span>参考答案</span><p>{row.ground_truth}</p></div>}
          </section>

          <section className="trace-statements-section" aria-labelledby="trace-statements-title">
            <div className="trace-section-heading"><h4 id="trace-statements-title">逐句判定</h4><span>来自原始溯源记录</span></div>
            {statements && statements.length > 0 ? <div className="trace-statements">
              {statements.map((statement, index) => {
                const matchingContext = statement.verdict === 'supported' && statement.evidence.trim()
                  ? contexts.findIndex((context) => context.text.includes(statement.evidence)) : -1;
                return <article className={`trace-statement trace-statement--${statement.verdict}`} key={index}>
                  <div className="trace-statement-heading"><span>断言 {index + 1}</span><span className={`badge trace-verdict trace-verdict--${statement.verdict}`}>{verdictLabels[statement.verdict]}</span></div>
                  <p className="trace-statement-text">{statement.text}</p>
                  {statement.verdict === 'unknown' && statement.originalVerdict && <p className="trace-unknown-verdict">原始判定值：{statement.originalVerdict}</p>}
                  <div className="trace-evidence">
                    <div className="trace-evidence-label"><Icon name="file" size={14} /><span>原始判定证据</span></div>
                    {statement.evidence.trim() ? <blockquote>{statement.evidence}</blockquote> : <p className="trace-inline-empty">原始记录未提供证据。</p>}
                    {matchingContext >= 0 && <p className="trace-evidence-match"><Icon name="check" size={13} /><span>{contexts[matchingContext].originalText ? '精确匹配检索上下文原文' : `精确匹配检索片段 ${matchingContext + 1}`}</span></p>}
                    {statement.verdict === 'supported' && statement.evidence.trim() && matchingContext < 0 && <p className="trace-evidence-unmatched">未在当前检索上下文中找到精确匹配。</p>}
                  </div>
                </article>;
              })}
            </div> : <div className="trace-no-statements"><Icon name="trace" size={22} /><div><h5>暂无逐句溯源</h5><p>当前记录没有可读取的逐句判定，不能据此判断幻觉情况。</p></div></div>}
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
