import type { CSSProperties } from 'react';
import type { EvaluationResult, ResultRow } from '../api/types';
import { MODEL_COLORS, MODELS } from '../config';

export const numberValue = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

export function percent(value: unknown): string {
  const number = numberValue(value);
  return number === null ? '—' : `${(number * 100).toFixed(1)}%`;
}

export function count(value: unknown): string {
  const number = numberValue(value);
  return number === null ? '—' : number.toFixed(0);
}

export function seconds(value: unknown): string {
  const number = numberValue(value);
  return number === null ? '—' : `${number.toFixed(1)}s`;
}

export function modelName(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—';
  return MODELS.find((model) => model.id === value)?.label ?? value;
}

/** Swatch colour for a model option; it follows the model's position in the result, as the charts do. */
export function swatch(index: number): CSSProperties | undefined {
  return index < 0 ? undefined : { '--swatch': MODEL_COLORS[index % MODEL_COLORS.length] } as CSSProperties;
}

export function summaryModel(result: EvaluationResult): ResultRow | undefined {
  return result.summary.find((row) => numberValue(row.faithfulness) !== null);
}

export function textValue(value: unknown, fallback = '—'): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback;
}
