import { useState } from 'react';
import type { ReactNode } from 'react';
import type { DemoFixture, EvaluationResult } from '../api/types';
import type { WorkspaceConfig } from '../state';
import { Icon } from '../components/Icon';
import { SlideArrow } from '../components/SlideArrow';
import { MetricBars } from '../components/MetricBars';
import { count, modelName, numberValue, percent, seconds, textValue } from '../utils/format';

function summaryValue(value: unknown, format: (value: unknown) => string): string {
  return numberValue(value) === null ? '暂无数据' : format(value);
}

function displayRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

export function OverviewView({ result, onConfigure, onDemo, onCompare, onTrace, demoDisabled = false, resultMeta, gateProvenance, gateRequest }: { result: EvaluationResult | null; onConfigure: () => void; onDemo: () => void; onCompare: () => void; onTrace: () => void; demoDisabled?: boolean; resultMeta?: ReactNode; gateProvenance?: DemoFixture['provenance']; gateRequest?: WorkspaceConfig | null }) {
  const [selection, setSelection] = useState<{ result: EvaluationResult | null; model: string }>({ result: null, model: '' });
  if (!result) return <section className="initial-state"><div className="initial-mark"><Icon name="shield" size={34}/></div><div><h2>暂无评测结果</h2><p>完成一次评测，查看模型能力、幻觉证据与质量门禁。</p><div className="initial-actions"><button className="btn btn-primary" onClick={onConfigure}><Icon name="play"/>配置评测</button><button className="btn btn-secondary" onClick={onDemo} disabled={demoDisabled}>查看演示结果</button></div></div><div className="initial-guide"><span>01 数据</span><Icon name="chevron" size={14}/><span>02 模型</span><Icon name="chevron" size={14}/><span>03 评测</span><Icon name="chevron" size={14}/><span>04 证据</span></div></section>;
  const models = result.summary.filter((row) => typeof row.model === 'string' && row.model.length > 0);
  const defaultModel = models.find((row) => numberValue(row.faithfulness) !== null);
  const selected = selection.result === result
    ? models.find((row) => row.model === selection.model) ?? defaultModel
    : defaultModel;
  const selectedModel = typeof selected?.model === 'string' ? selected.model : '';
  const gateRows = selectedModel ? result.model_dfs[selectedModel] : undefined;
  const positiveCount = Array.isArray(gateRows)
    ? gateRows.filter((row) => row.case_type === 'positive').length : null;
  const validPositiveCount = (key: string) => Array.isArray(gateRows)
    ? gateRows.filter((row) => row.case_type === 'positive' && numberValue(row[key]) !== null).length : null;
  const validFaithCount = validPositiveCount('faithfulness');
  const validContextCount = validPositiveCount('context_precision');
  const validCountLabel = (value: number | null) => value === null || positiveCount === null
    ? '有效记录数量未返回' : `有效记录 ${value} / ${positiveCount}`;
  const snapshotRows = gateProvenance ? Object.values(result.model_dfs).flat() : [];
  const snapshotQuestionCount = new Set(snapshotRows.map((row) => row.question)).size;
  const snapshotThresholds = displayRecord(displayRecord(gateProvenance?.run)?.thresholds);
  const manualThresholds = gateRequest?.useDefaultThresholds === false ? gateRequest : null;
  const faithGateThreshold = numberValue(gateProvenance
    ? snapshotThresholds?.faithfulness : manualThresholds?.faithThreshold);
  const contextGateThreshold = numberValue(gateProvenance
    ? snapshotThresholds?.context_precision : manualThresholds?.contextThreshold);
  const thresholdNote = gateProvenance && (faithGateThreshold !== null || contextGateThreshold !== null)
    ? '已有阈值来自演示快照的原始运行记录；缺失项不作推断。'
    : manualThresholds ? '阈值来自本次显式提交参数。' : '未随结果返回的运行阈值不作推断。';
  // 能力图上的门禁参考线：只有两项阈值都取得到时才画，缺失项不作推断。
  const gateLine: readonly [number, number] | undefined = faithGateThreshold !== null && contextGateThreshold !== null
    ? [faithGateThreshold, contextGateThreshold] : undefined;
  const positiveFaithfulness = numberValue(selected?.pos_faithfulness);
  const positiveContextPrecision = numberValue(selected?.pos_context_precision);
  // This display check never changes the engine's recorded gate_passed.
  const currentModelGatePassed = positiveFaithfulness !== null && positiveContextPrecision !== null
    && faithGateThreshold !== null && contextGateThreshold !== null
    ? positiveFaithfulness >= faithGateThreshold && positiveContextPrecision >= contextGateThreshold : null;
  const gateStatus = currentModelGatePassed === null ? 'WARNING' : currentModelGatePassed ? 'PASS' : 'FAIL';
  const gateClass = currentModelGatePassed === null ? 'uncertain' : currentModelGatePassed ? 'passed' : 'blocked';
  const belowThreshold = [
    positiveFaithfulness !== null && faithGateThreshold !== null && positiveFaithfulness < faithGateThreshold ? '正向忠实度' : '',
    positiveContextPrecision !== null && contextGateThreshold !== null && positiveContextPrecision < contextGateThreshold ? '正向上下文精度' : '',
  ].filter(Boolean);
  const gateReason = currentModelGatePassed === null ? '正向指标或阈值缺失，无法完成检查'
    : currentModelGatePassed ? '两项正向指标均达到门槛' : `${belowThreshold.join('、')}低于门槛`;
  const cards = [
    { label: '幻觉语句数', value: summaryValue(selected?.hallucination_statement_count, count), note: '现有逐句统计' },
    { label: '事实错误数', value: summaryValue(selected?.trace_fact_error_count, count), note: '与文档事实不一致' },
    { label: '无依据编造数', value: summaryValue(selected?.trace_fabrication_count, count), note: '缺少上下文支持' },
    { label: '拒答准确率', value: summaryValue(selected?.refusal_accuracy, percent), note: '负向用例表现' },
  ];
  return <>
    <div className="result-context"><div className="summary-model-control"><label htmlFor="summary-model">当前摘要模型</label><select id="summary-model" value={selectedModel} disabled={!models.length} onChange={(event) => setSelection({ result, model: event.target.value })}>{!selectedModel && <option value="" disabled>暂无有效摘要模型</option>}{models.map((row) => <option key={String(row.model)} value={String(row.model)}>{modelName(row.model)}</option>)}</select></div>{resultMeta}</div>
    <section className={`quality-gate ${gateClass}`} aria-labelledby="current-gate-title">
      <div className="gate-summary">
        <div className="gate-status-block">
          <h2 className="gate-label" id="current-gate-title"><Icon name="shield" size={16}/>当前模型质量门禁</h2>
          <div className="gate-status" role="status" aria-live="polite" aria-atomic="true">
            <strong className="gate-value">{gateStatus}</strong>
            <span className="gate-description">{currentModelGatePassed === null ? '无法判定' : currentModelGatePassed ? '通过规则检查' : '未达到门槛'}</span>
          </div>
        </div>
        <div className="gate-evaluation">
          <div className="gate-context" role="group" aria-label="门禁检查上下文">
            <dl className="gate-model-context">
              <div className="gate-subject"><dt>判定模型</dt><dd>{selectedModel ? modelName(selectedModel) : '暂无有效模型'}</dd></div>
              <div className="gate-range"><dt>{gateProvenance ? '演示门禁范围' : '门禁检查范围'}</dt><dd>{positiveCount === null ? '正向用例 · 数量未返回' : `${positiveCount} 条正向用例`}</dd></div>
            </dl>
            <dl className="gate-metrics">
              <div data-gate-field="pos_faithfulness"><dt>正向忠实度</dt><dd><span>{summaryValue(positiveFaithfulness, percent)}</span><small>{faithGateThreshold === null ? '门槛未提供' : `门槛 ≥ ${percent(faithGateThreshold)}`}</small><small>{validCountLabel(validFaithCount)}</small></dd></div>
              <div data-gate-field="pos_context_precision"><dt>正向上下文精度</dt><dd><span>{summaryValue(positiveContextPrecision, percent)}</span><small>{contextGateThreshold === null ? '门槛未提供' : `门槛 ≥ ${percent(contextGateThreshold)}`}</small><small>{validCountLabel(validContextCount)}</small></dd></div>
            </dl>
          </div>
          <p className="gate-reason">{gateReason}</p>
        </div>
      </div>
      {gateProvenance && <p className="gate-snapshot-scope">当前快照包含 {snapshotQuestionCount} 个问题、{Object.keys(result.model_dfs).length} 个模型、{snapshotRows.length} 条回答。</p>}
      <p className="gate-interpretation">{gateProvenance ? '本快照用于展示评测流程；PASS 仅表示指定正向样本达到现有门槛，不代表模型总体可靠性。' : '门禁结论仅反映规则检查，不代表所有模型指标优秀。'}</p>
      <details className="gate-details"><summary><Icon name="shield" size={16}/>门禁依据与原始记录<Icon name="chevron" size={15}/></summary><div><p className="muted result-context-note">当前模型用于指标查看，不代表最佳模型；门禁展示按该模型的正向指标及已有阈值检查，原始记录保持不变。</p><p className="original-gate">{`原始评测门禁：${result.gate_passed ? 'PASS' : 'FAIL'}`}</p><p>{textValue(result.gate_message, '原始门禁说明未记录。')}</p><p className="muted">原始门禁采用首个有效评测模型的正向指标；当前卡片检查所选模型，不改写原始记录。</p><p className="muted">{thresholdNote}</p><p className="muted">下列两项属于当前摘要模型，不用于重写原始评测门禁。</p><dl><div><dt>正向忠实度</dt><dd>{summaryValue(selected?.pos_faithfulness, percent)}</dd></div><div><dt>正向上下文精度</dt><dd>{summaryValue(selected?.pos_context_precision, percent)}</dd></div><div><dt>总评测耗时</dt><dd>{seconds(result.total_elapsed_seconds)}</dd></div></dl></div></details>
    </section>
    <section className="kpi-strip" aria-label="评测核心指标">
      {cards.map((card) => <div className="metric-tile" key={card.label}><span>{card.label}</span><strong className={card.value === '暂无数据' ? 'metric-missing' : card.value === '0' ? 'metric-zero' : undefined}>{card.value}</strong><small>{card.note}</small></div>)}
    </section>
    <section className="ability-section"><div className="section-heading"><div><h2>模型评测能力分析</h2><p>图中整体均值用于模型横向比较；当前模型门禁仅检查正向忠实度与正向上下文精度。</p></div><span className="subtle-tag">数值越高表现越好</span></div><MetricBars rows={result.summary_df} gateLine={gateLine}/></section>
    <div className="overview-bottom">
      <button className="analysis-entry" onClick={onCompare}><Icon name="chart" size={24}/><span><strong>比较模型表现</strong><small>查看完整指标与模型耗时</small></span><SlideArrow/></button>
      <button className="analysis-entry" onClick={onTrace}><Icon name="trace" size={24}/><span><strong>检查幻觉证据</strong><small>展开回答、原子陈述与检索原文</small></span><SlideArrow/></button>
    </div>
  </>;
}
