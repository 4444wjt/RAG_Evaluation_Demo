import { Icon } from '../../components/Icon';
import { DEEPSEEK_VERSION } from '../../config';

// Presentation-only transcription of the two original UI captures from snapshot
// 20260930-1958. No evaluation, gate calculation or live data loading occurs here.
// Native text stays sharp at the display width instead of resampling a bitmap.
export function QualityGatePreview() {
  return <section className="quality-gate passed landing-product-preview landing-gate-preview" aria-label={`${DEEPSEEK_VERSION} 演示快照质量门禁`}>
    <div className="gate-summary">
      <div className="gate-status-block">
        <h4 className="gate-label"><Icon name="shield" size={16}/>当前模型质量门禁</h4>
        <div className="gate-status">
          <strong className="gate-value">PASS</strong>
          <span className="gate-description">通过规则检查</span>
        </div>
      </div>
      <div className="gate-evaluation">
        <div className="gate-context">
          <dl className="gate-model-context">
            <div className="gate-subject"><dt>判定模型</dt><dd>{DEEPSEEK_VERSION}</dd></div>
            <div className="gate-range"><dt>演示门禁范围</dt><dd>3 条正向用例</dd></div>
          </dl>
          <dl className="gate-metrics">
            <div data-gate-field="pos_faithfulness"><dt>正向忠实度</dt><dd><span>100.0%</span><small>门槛 ≥ 80.0%</small></dd></div>
            <div data-gate-field="pos_context_precision"><dt>正向上下文精度</dt><dd><span>100.0%</span><small>门槛 ≥ 80.0%</small></dd></div>
          </dl>
        </div>
        <p className="gate-reason">两项正向指标均达到门槛</p>
      </div>
    </div>
    <p className="gate-snapshot-scope">当前快照包含 6 个问题、3 个模型、18 条回答。</p>
    <p className="gate-interpretation">本快照用于展示评测流程；PASS 仅表示指定正向样本达到现有门槛，不代表模型总体可靠性。</p>
    <div className="landing-gate-record"><Icon name="shield" size={16}/><span>门禁依据与原始记录</span><Icon name="chevron" size={15}/></div>
  </section>;
}

export function EvidenceTracePreview() {
  return <section className="trace-statements-section landing-product-preview landing-trace-preview" aria-label={`${DEEPSEEK_VERSION} 演示快照逐句判定`}>
    <div className="trace-section-heading"><h4>逐句判定</h4><span>来自原始溯源记录</span></div>
    <div className="trace-statements">
      <article className="trace-statement trace-statement--supported">
        <div className="trace-statement-heading"><span>陈述 1</span><span className="badge trace-verdict trace-verdict--supported">有依据</span></div>
        <p className="trace-statement-text">星云科技最新一轮B轮融资金额达5亿元人民币。</p>
        <div className="trace-evidence">
          <div className="trace-evidence-label"><Icon name="file" size={14}/><span>原始判定证据</span></div>
          <blockquote>星云科技已累计获得三轮融资，最新一轮B轮融资金额达5亿元人民币。</blockquote>
          <p className="trace-evidence-match"><Icon name="check" size={13}/><span>精确匹配检索上下文原文</span></p>
        </div>
      </article>
    </div>
  </section>;
}
