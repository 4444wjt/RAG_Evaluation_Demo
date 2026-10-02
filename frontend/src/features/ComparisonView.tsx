import type { EvaluationResult, JsonValue, ResultRow } from '../api/types';
import { AbilityChart, DurationChart, RiskChart } from '../components/Charts';
import { Icon } from '../components/Icon';
import { count, modelName, numberValue, percent, seconds } from '../utils/format';
import './comparison.css';

const COLUMN_LABELS: Record<string, string> = {
  faithfulness: '忠实度',
  context_precision: '上下文精度',
  answer_relevancy: '答案相关性',
  answer_correctness: '事实一致性',
  context_recall: '回答完整性',
  pos_faithfulness: '正例忠实度',
  pos_context_precision: '正例上下文精度',
  neg_faithfulness: '负例忠实度',
  neg_context_precision: '负例上下文精度',
  refusal_accuracy: '拒答准确率',
  hallucination_rate: '幻觉率',
  score: '原始评分',
  hallucination_statement_count: '幻觉声明数',
  hallucination_ratio: '声明幻觉比例',
  trace_statement_count: '追踪声明数',
  trace_fact_error_count: '事实错误数',
  trace_fabrication_count: '编造声明数',
  pos_trace_hallucination_ratio: '正例追踪幻觉比例',
  elapsed_seconds: '评测耗时',
};

const RATIO_COLUMNS = new Set([
  'faithfulness', 'context_precision', 'answer_relevancy', 'answer_correctness', 'context_recall',
  'pos_faithfulness', 'pos_context_precision', 'neg_faithfulness', 'neg_context_precision',
  'refusal_accuracy', 'hallucination_rate', 'hallucination_ratio', 'pos_trace_hallucination_ratio',
]);

function formatCell(key: string, value: JsonValue | undefined): string {
  if (value === null || value === undefined) return '—';
  if (RATIO_COLUMNS.has(key)) return percent(value);
  if (key === 'elapsed_seconds') return seconds(value);
  if (key.endsWith('_count')) return count(value);
  if (typeof value === 'number') {
    return numberValue(value) === null ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
  }
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value || '—';
  return JSON.stringify(value);
}

function OriginalMetricsTable({ rows }: { rows: ResultRow[] }) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((key) => key !== 'model');

  return <div className="comparison-table-scroll" tabIndex={0} role="region" aria-label="完整模型指标表，可横向滚动">
    <table className="comparison-table">
      <caption className="comparison-sr-only">各模型的全部原始指标。百分比和秒仅作格式化展示，破折号表示未提供数值。</caption>
      <thead>
        <tr>
          <th scope="col" className="comparison-model-cell">模型</th>
          {columns.map((key) => <th key={key} scope="col" title={key}>
            <span>{COLUMN_LABELS[key] ?? key}</span>
            <small>{RATIO_COLUMNS.has(key) ? '%' : key === 'elapsed_seconds' ? '秒' : key.endsWith('_count') ? '条' : '原值'}</small>
          </th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => <tr key={`${typeof row.model === 'string' ? row.model : 'model'}-${index}`}>
          <th scope="row" className="comparison-model-cell">
            <span>{modelName(row.model)}</span>
            {typeof row.model === 'string' && row.model !== modelName(row.model) && <small title={row.model}>{row.model}</small>}
          </th>
          {columns.map((key) => {
            const value = formatCell(key, row[key]);
            return <td key={key} className={value === '—' ? 'comparison-missing' : ''}><span title={value}>{value}</span></td>;
          })}
        </tr>)}
      </tbody>
    </table>
  </div>;
}

export function ComparisonView({ result }: { result: EvaluationResult | null }) {
  const rows = result?.summary_df ?? [];

  return <div className="comparison-view">
    <header className="comparison-header">
      <p>从能力、风险与耗时查看同一批次的评测结果。</p>
      {rows.length > 0 && <span className="comparison-model-count">{rows.length} 个模型</span>}
    </header>

    {rows.length === 0 ? <section className="comparison-empty empty-state" aria-label="暂无模型对比结果">
      <Icon name="chart" size={32} />
      <h2>{result ? '本次结果没有模型汇总' : '还没有可对比的结果'}</h2>
      <p>运行一次评测或加载示例结果，即可查看模型能力、风险指标和完整数据。</p>
    </section> : <>
      <section className="comparison-panel comparison-main-panel" aria-labelledby="comparison-ability-title">
        <div className="comparison-panel-heading section-heading">
          <div><h2 id="comparison-ability-title">模型能力</h2><p>五项能力分别比较，百分比越高越好。</p></div>
          <span className="comparison-source-note">Ragas 指标</span>
        </div>
        <AbilityChart rows={rows} />
        <p className="comparison-chart-note">缺失指标不绘制；完整数值可在下方指标表中查看。</p>
      </section>

      <div className="comparison-support-grid">
        <section className="comparison-panel" aria-labelledby="comparison-risk-title">
          <div className="comparison-panel-heading section-heading"><div><h2 id="comparison-risk-title">风险指标</h2><p>幻觉率越低越好；拒答准确率越高越好。</p></div></div>
          <RiskChart rows={rows} />
        </section>
        <section className="comparison-panel" aria-labelledby="comparison-duration-title">
          <div className="comparison-panel-heading section-heading"><div><h2 id="comparison-duration-title">评测耗时</h2><p>各模型本次评测总耗时，单位为秒。</p></div></div>
          <DurationChart rows={rows} />
        </section>
      </div>

      <section className="comparison-panel comparison-table-panel" aria-labelledby="comparison-metrics-title">
        <div className="comparison-panel-heading section-heading"><div><h2 id="comparison-metrics-title">完整指标</h2><p>保留全部原始指标与追踪字段，横向滚动查看更多。</p></div><span className="comparison-source-note">— 表示缺失</span></div>
        <OriginalMetricsTable rows={rows} />
      </section>
    </>}
  </div>;
}
