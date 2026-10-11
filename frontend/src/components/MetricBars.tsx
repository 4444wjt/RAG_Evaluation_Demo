import type { CSSProperties } from 'react';
import type { ResultRow } from '../api/types';
import { METRICS, MODEL_COLORS } from '../config';
import { modelName, numberValue } from '../utils/format';
import { Icon } from './Icon';
import './viz.css';

/** Compact percentage: whole values drop ".0" so side-by-side figures stay aligned. */
export function ratioText(value: number): string {
  const scaled = Math.round(value * 1000) / 10;
  return `${Number.isInteger(scaled) ? scaled.toFixed(0) : scaled.toFixed(1)}%`;
}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

/**
 * One row per metric, one thin bar per model. Replaces a 15-bar grouped chart: the gate
 * threshold sits on exactly the two rows the gate checks, and missing values stay missing.
 */
export function MetricBars({ rows, gateLine }: { rows: ResultRow[]; gateLine?: readonly [number, number] }) {
  const models = rows.map((row, index) => ({ key: `${String(row.model)}-${index}`, name: modelName(row.model), color: MODEL_COLORS[index % MODEL_COLORS.length], row }));
  const thresholds: Array<number | undefined> = [gateLine?.[0], gateLine?.[1]];
  const available = rows.some((row) => METRICS.some((metric) => numberValue(row[metric.key]) !== null));
  if (!available) return <div className="evaluation-chart-unavailable metric-bars-unavailable" role="status"><Icon name="chart" size={26} /><strong>能力指标暂不可用</strong><span>评测结果未提供这些指标，缺失值不会显示为 0。</span></div>;

  return <figure className="metric-bars">
    <figcaption className="viz-legend">
      {models.map((model) => <span key={model.key}><i style={{ background: model.color }} />{model.name}</span>)}
      {gateLine && <span><i className="metric-bars-key-tick" />门禁门槛</span>}
    </figcaption>
    <dl>
      {METRICS.map((metric, metricIndex) => {
        const threshold = thresholds[metricIndex];
        return <div className="metric-bars-row" key={metric.key}>
          <dt>{metric.label}{threshold !== undefined && <small>{`门禁线 ${ratioText(threshold)}`}</small>}</dt>
          <dd>{models.map((model) => {
            const value = numberValue(model.row[metric.key]);
            const label = `${model.name} ${metric.label} ${value === null ? '未提供' : ratioText(value)}`;
            return <div className="metric-bar" key={model.key} role="img" aria-label={label} title={label}>
              <span className="metric-bar-track">
                {value !== null && <span className="metric-bar-fill" style={{ '--value': clamp01(value), '--bar-color': model.color } as CSSProperties} />}
                {threshold !== undefined && <span className="metric-bar-tick" style={{ left: `${clamp01(threshold) * 100}%` }} />}
              </span>
              <span className={`metric-bar-value${value === null ? ' is-missing' : ''}`}>{value === null ? '—' : ratioText(value)}</span>
            </div>;
          })}</dd>
        </div>;
      })}
    </dl>
  </figure>;
}
