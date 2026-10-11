import type { CSSProperties } from 'react';
import type { ResultRow } from '../api/types';
import { METRICS, MODEL_COLORS } from '../config';
import { modelName, numberValue } from '../utils/format';
import { ratioText } from './MetricBars';
import './viz.css';

/** Ratio metrics shown in the matrix. `lowerIsBetter` flips the shading so a deeper cell always means a better result. */
const COLUMNS: Array<{ key: string; label: string; lowerIsBetter?: boolean }> = [
  ...METRICS.map((metric) => ({ key: metric.key, label: metric.label })),
  { key: 'hallucination_rate', label: '幻觉率', lowerIsBetter: true },
  { key: 'refusal_accuracy', label: '拒答准确率' },
];

/**
 * Model × metric matrix on one monochrome luminance ramp. Values stay as text in every cell, so
 * colour is never the only carrier; missing values are shown as "—" and left unshaded.
 */
export function HeatMatrix({ rows }: { rows: ResultRow[] }) {
  const columns = COLUMNS.filter((column) => rows.some((row) => numberValue(row[column.key]) !== null));
  if (!rows.length || !columns.length) return null;
  return <div className="heat-matrix-scroll">
    <table className="heat-matrix">
      <caption className="viz-sr-only">各模型比率指标矩阵。颜色越亮表示表现越好；幻觉率按越低越好着色。</caption>
      <thead><tr><th scope="col">模型</th>{columns.map((column) => <th scope="col" key={column.key}>{column.label}{column.lowerIsBetter && <small>越低越好</small>}</th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={`${String(row.model)}-${index}`}>
        <th scope="row"><i style={{ background: MODEL_COLORS[index % MODEL_COLORS.length] }} />{modelName(row.model)}</th>
        {columns.map((column) => {
          const value = numberValue(row[column.key]);
          if (value === null) return <td key={column.key} className="is-missing">—</td>;
          const goodness = Math.min(1, Math.max(0, column.lowerIsBetter ? 1 - value : value));
          return <td key={column.key} className={goodness >= 0.6 ? 'is-strong' : undefined} style={{ '--heat': goodness.toFixed(3) } as CSSProperties} title={`${modelName(row.model)} ${column.label} ${ratioText(value)}`}>{ratioText(value)}</td>;
        })}
      </tr>)}</tbody>
    </table>
  </div>;
}
