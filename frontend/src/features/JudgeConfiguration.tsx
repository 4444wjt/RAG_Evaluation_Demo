import './judge-configuration.css';

interface Choice { id: string; label: string }

export function JudgeConfiguration({ value, models, testedModels, onChange }: {
  value?: string; models: Choice[]; testedModels: string[]; onChange: (name: string) => void;
}) {
  const missing = Boolean(value) && !models.some((model) => model.id === value);
  return <div className="judge-configuration">
    <div className="judge-configuration-heading"><label htmlFor="evaluation-judge">评测裁判</label><span>五项指标与 Trace</span></div>
    <select id="evaluation-judge" value={value ?? ''} aria-describedby="evaluation-judge-help" onChange={(event) => onChange(event.target.value)}>
      <option value="">沿用各自被测模型（原有方式）</option>
      {missing && <option value={value}>{value}（当前未登记）</option>}
      {models.map((model) => <option key={model.id} value={model.id}>{model.label} · {model.id}</option>)}
    </select>
    <p id="evaluation-judge-help">回答由上方被测模型生成；单独选择裁判后，五项指标和逐句核对统一交给该裁判。裁判可以不参与回答生成。</p>
    <p>裁判会收到回答与检索资料；私有资料请先确认裁判的部署位置。</p>
    {missing ? <p className="judge-configuration-warning" role="status">所选裁判当前不在模型登记中，请重新选择或先登记。</p>
      : value && testedModels.includes(value) ? <p className="judge-configuration-warning">所选裁判也在被测模型中，对该模型仍属于同模型核对。</p>
      : value ? <p className="judge-configuration-note">已单独配置裁判；不同模型仍可能有共同偏差，不代表人工认证。</p> : null}
  </div>;
}
