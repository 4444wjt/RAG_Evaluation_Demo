import './viz.css';

export type DonutSegment = { key: string; label: string; value: number; color: string };

/** Part-to-whole ring with a centre total and a labelled legend; every count is also written as text. */
export function Donut({ segments, centerValue, centerLabel, label }: { segments: DonutSegment[]; centerValue: string; centerLabel: string; label: string }) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  const radius = 44;
  const circumference = 2 * Math.PI * radius;
  const visible = segments.filter((segment) => segment.value > 0);
  const gap = visible.length > 1 ? 3 : 0;
  let offset = 0;
  return <div className="donut">
    <svg viewBox="0 0 120 120" role="img" aria-label={label}>
      <circle cx="60" cy="60" r={radius} className="donut-track" />
      {total > 0 && visible.map((segment) => {
        const length = (segment.value / total) * circumference;
        const arc = <circle key={segment.key} cx="60" cy="60" r={radius} className="donut-arc" style={{ stroke: segment.color }} strokeDasharray={`${Math.max(0, length - gap)} ${circumference}`} strokeDashoffset={-offset} transform="rotate(-90 60 60)" />;
        offset += length;
        return arc;
      })}
      <text x="60" y="58" textAnchor="middle" className="donut-value">{centerValue}</text>
      <text x="60" y="76" textAnchor="middle" className="donut-caption">{centerLabel}</text>
    </svg>
    <ul className="donut-legend">{segments.map((segment) => <li key={segment.key}><i style={{ background: segment.color }} /><span>{segment.label}</span><strong>{segment.value}</strong></li>)}</ul>
  </div>;
}
