import type { ResultRow } from '../api/types';
import './judge-configuration.css';

export function JudgeProvenance({ rows }: { rows: ResultRow[] }) {
  const recorded = rows.filter((row) => typeof row.judge_model === 'string');
  if (!recorded.length) return null;
  return <section className="judge-provenance" aria-label="裁判来源">
    <h3>裁判来源</h3>
    {recorded.map((row, index) => <dl className="judge-provenance-fields" key={`${String(row.model ?? row.judge_model)}-${index}`}>
      {typeof row.model === 'string' && <div><dt>被测模型</dt><dd>{row.model}</dd></div>}
      <div><dt>裁判方式</dt><dd>{row.judge_mode === 'configured' ? '单独配置' : row.judge_mode === 'self' ? '沿用被测模型' : '未记录'}</dd></div>
      <div><dt>裁判请求模型</dt><dd>{String(row.judge_model)}</dd></div>
      <div><dt>{row.judge_response_scope === 'model_batch' ? '实际响应模型（本模型批次）' : '实际响应模型'}</dt><dd>{Array.isArray(row.judge_response_models) && row.judge_response_models.length ? row.judge_response_models.join('、') : '未知（响应未记录）'}</dd></div>
    </dl>)}
    <p>单独配置不代表判定已获独立认证；实际响应名称未知时不以请求名称代填。</p>
  </section>;
}
