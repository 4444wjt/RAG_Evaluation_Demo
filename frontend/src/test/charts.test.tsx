import { act, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as echarts from 'echarts/core';
import type { EvaluationResult, ResultRow } from '../api/types';
import { AbilityChart, DurationChart, RiskChart } from '../components/Charts';
import { HeatMatrix } from '../components/HeatMatrix';
import { ComparisonView } from '../features/ComparisonView';
import { METRICS } from '../config';
import fixture from '../fixtures/demo.json';
import { modelName } from '../utils/format';

vi.mock('echarts/core', () => ({ init: vi.fn(), use: vi.fn() }));

type ChartTextStyle = { color: string; fontFamily: string; fontSize: number };

type ChartOption = {
  animation: boolean;
  backgroundColor: string;
  color: string[];
  legend?: { data?: string[]; top: number; left: string; icon: string; textStyle: ChartTextStyle };
  aria: { enabled: boolean };
  series: Array<{
    name: string;
    data: Array<number | null | { value: number | null; itemStyle?: { color: string } }>;
    itemStyle?: { color: string };
    label?: ChartTextStyle & { show: boolean; position?: string; formatter: (params: { value: number | null }) => string };
  }>;
  tooltip: {
    renderMode: string;
    backgroundColor: string;
    borderColor: string;
    textStyle: ChartTextStyle;
    axisPointer: { shadowStyle: { color: string } };
    valueFormatter: (value: unknown) => string;
  };
  xAxis: { data?: string[]; axisLabel: ChartTextStyle; axisLine: { lineStyle: { color: string } } };
  yAxis: { min?: number; max?: number; data?: string[]; axisLabel: ChartTextStyle; splitLine?: { lineStyle: { color: string } }; axisLine?: { lineStyle: { color: string } } };
  media: Array<{ query?: { maxWidth: number }; option: { series?: Array<{ label: { show: boolean } }> } }>;
};

const chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
const resizeObservers: MockResizeObserver[] = [];
const chartThemeTokens = {
  '--font-family': '"UI Theme Test", sans-serif',
  '--font-size-label': '14px',
  '--font-size-caption': '11px',
  '--chart-text': '#f5f7fa',
  '--chart-axis': '#a7b0be',
  '--chart-grid': 'rgba(255, 255, 255, 0.08)',
  '--chart-tooltip-bg': '#1b2430',
  '--chart-tooltip-border': 'rgba(255, 255, 255, 0.16)',
  '--chart-tooltip-text': '#f5f7fa',
  '--chart-pointer': 'rgba(255, 255, 255, 0.04)',
  '--chart-background': 'transparent',
};
const originalModelColors = ['#3987e5', '#d95926', '#199e70', '#9085e9', '#d55181', '#c98500'];

class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    resizeObservers.push(this);
  }
  trigger() { this.callback([], this as unknown as ResizeObserver); }
}

function latestOption(): ChartOption {
  return chart.setOption.mock.calls.at(-1)?.[0] as ChartOption;
}

beforeEach(() => {
  chart.setOption.mockReset();
  chart.resize.mockReset();
  chart.dispose.mockReset();
  resizeObservers.length = 0;
  vi.mocked(echarts.init).mockReset();
  vi.mocked(echarts.init).mockReturnValue(chart as unknown as ReturnType<typeof echarts.init>);
  vi.stubGlobal('ResizeObserver', MockResizeObserver);
});

afterEach(() => {
  Object.keys(chartThemeTokens).forEach((token) => document.documentElement.style.removeProperty(token));
  document.documentElement.style.removeProperty('--brand');
});

describe('evaluation charts', () => {
  it.each([
    ['ability', AbilityChart],
    ['risk', RiskChart],
    ['duration', DurationChart],
  ] as const)('reads the CSS environment theme for the %s chart without changing model colors', (_, Component) => {
    Object.entries(chartThemeTokens).forEach(([token, value]) => document.documentElement.style.setProperty(token, value));
    document.documentElement.style.setProperty('--brand', '#f2c879');
    render(<Component rows={fixture.result.summary_df} />);
    const option = latestOption();

    expect(option.backgroundColor).toBe(chartThemeTokens['--chart-background']);
    expect(option.xAxis.axisLabel.color).toBe(chartThemeTokens['--chart-axis']);
    expect(option.yAxis.axisLabel.color).toBe(chartThemeTokens['--chart-axis']);
    expect(option.xAxis.axisLine.lineStyle.color).toBe(chartThemeTokens['--chart-grid']);
    if (option.yAxis.splitLine) {
      expect(option.yAxis.splitLine.lineStyle.color).toBe(chartThemeTokens['--chart-grid']);
    }
    expect(option.tooltip.backgroundColor).toBe(chartThemeTokens['--chart-tooltip-bg']);
    expect(option.tooltip.borderColor).toBe(chartThemeTokens['--chart-tooltip-border']);
    expect(option.tooltip.textStyle.color).toBe(chartThemeTokens['--chart-tooltip-text']);
    expect(option.tooltip.axisPointer.shadowStyle.color).toBe(chartThemeTokens['--chart-pointer']);
    if (option.legend) {
      expect(option.legend.textStyle).toMatchObject({ color: chartThemeTokens['--chart-text'], fontFamily: chartThemeTokens['--font-family'], fontSize: 14 });
    }
    expect(option.tooltip.textStyle).toMatchObject({ fontFamily: chartThemeTokens['--font-family'], fontSize: 14 });
    expect(option.xAxis.axisLabel).toMatchObject({ fontFamily: chartThemeTokens['--font-family'], fontSize: 11 });
    expect(option.yAxis.axisLabel).toMatchObject({ fontFamily: chartThemeTokens['--font-family'], fontSize: 11 });
    if (option.series[0].label) {
      expect(option.series[0].label).toMatchObject({ color: chartThemeTokens['--chart-text'], fontFamily: chartThemeTokens['--font-family'], fontSize: 11 });
    }
    expect(option.color).toEqual(originalModelColors);
    expect(option.color).not.toContain('#f2c879');
  });

  it.each([
    ['ability', AbilityChart],
    ['risk', RiskChart],
  ] as const)('preserves all six %s model colors, slot order and implicit legend mapping', (_, Component) => {
    const rows: ResultRow[] = Array.from({ length: 7 }, (_, index) => ({
      model: `model-slot-${index}`,
      faithfulness: 0.5,
      hallucination_rate: 0.2,
    }));
    render(<Component rows={rows} />);
    const option = latestOption();
    const expectedColors = [...originalModelColors, originalModelColors[0]];

    expect(option.color).toEqual(originalModelColors);
    expect(option.series.map((series) => series.itemStyle?.color)).toEqual(expectedColors);
    expect(option.series.map((series) => series.name)).toEqual(rows.map((row) => modelName(row.model)));
    expect(option.legend!).toMatchObject({ top: 8, left: 'center', icon: 'roundRect' });
    expect(option.legend!.data).toBeUndefined();
    option.series.forEach((series, index) => {
      expect(series.name).toBe(modelName(rows[index].model));
      expect(series.itemStyle?.color).toBe(expectedColors[index]);
    });
  });

  it('preserves duration bar color slots independently of the brand and model values', () => {
    const rows: ResultRow[] = Array.from({ length: 7 }, (_, index) => ({ model: `duration-slot-${index}`, elapsed_seconds: index === 2 ? null : index }));
    document.documentElement.style.setProperty('--brand', '#f2c879');
    render(<DurationChart rows={rows} />);
    const option = latestOption();
    const colors = option.series[0].data.map((datum) => typeof datum === 'object' && datum !== null ? datum.itemStyle?.color : undefined);

    expect(option.color).toEqual(originalModelColors);
    expect(colors).toEqual([...originalModelColors, originalModelColors[0]]);
    expect(colors[2]).toBe('#199e70');
    expect(option.yAxis.data).toEqual(rows.map((row) => modelName(row.model)));
    expect(option.series[0].name).toBe('评测耗时');
    expect(option.legend).toBeUndefined();
  });

  it('retains readable environment defaults when no CSS theme is available', () => {
    render(<AbilityChart rows={fixture.result.summary_df} />);
    const option = latestOption();

    expect(option.xAxis.axisLabel.color).toBe('#a7b0be');
    expect(option.yAxis.splitLine!.lineStyle.color).toBe('rgba(255, 255, 255, 0.08)');
    expect(option.tooltip.backgroundColor).toBe('#1b2430');
    expect(option.legend!.textStyle.color).toBe('#f5f7fa');
    expect(option.backgroundColor).toBe('transparent');
    expect(option.color).toEqual(originalModelColors);
  });

  it('uses the five existing metrics and original model order without a derived score', () => {
    const rows: ResultRow[] = fixture.result.summary_df;
    render(<AbilityChart rows={rows} />);
    const option = latestOption();

    expect(option.series.map((series) => series.name)).toEqual(rows.map((row) => modelName(row.model)));
    expect(option.xAxis.data).toEqual(METRICS.map((metric) => metric.label));
    expect(option.series).toHaveLength(rows.length);
    option.series.forEach((series, index) => {
      expect(series.data).toEqual(METRICS.map((metric) => rows[index][metric.key]));
    });
    expect(option.yAxis).toMatchObject({ min: 0, max: 1 });
    expect(option.animation).toBe(false);
    expect(option.aria.enabled).toBe(true);
    expect(echarts.init).toHaveBeenCalledWith(expect.any(HTMLElement), undefined, { renderer: 'svg' });
    expect(screen.getByRole('img')).toHaveStyle({ height: '500px' });
    expect(option.series[0].label?.show).toBe(true);
    expect(option.series[0].label?.formatter({ value: 0.498 })).toBe('49.8%');
    expect(option.media.find((item) => item.query?.maxWidth === 380)?.option.series?.every((series) => !series.label.show)).toBe(true);
  });

  it('supports a compact presentation height without changing chart values or model colors', () => {
    render(<AbilityChart rows={fixture.result.summary_df} height={360} />);
    expect(screen.getByRole('img')).toHaveStyle({ height: '360px' });
    expect(screen.getByRole('img').parentElement).toHaveStyle({ height: '360px' });
    expect(latestOption().color).toEqual(originalModelColors);
    latestOption().series.forEach((series, index) => {
      expect(series.data).toEqual(METRICS.map((metric) => fixture.result.summary_df[index][metric.key]));
    });
  });

  it('uses the requested compact height when ability metrics are unavailable', () => {
    render(<AbilityChart rows={[]} height={360} />);
    expect(screen.getByRole('status')).toHaveStyle({ minHeight: '360px' });
    expect(screen.getByText('能力指标暂不可用')).toBeVisible();
    expect(echarts.init).not.toHaveBeenCalled();
  });

  it('preserves null datapoints and distinguishes a true zero from unavailable metrics', () => {
    const rows: ResultRow[] = [
      { model: 'deepseek-chat', faithfulness: 0, context_precision: null, answer_relevancy: 0.4, answer_correctness: null },
      { model: 'glm-5.2-fast-preview', faithfulness: null, context_precision: 0.2, answer_relevancy: null, answer_correctness: 0.7, context_recall: 1 },
    ];
    render(<AbilityChart rows={rows} />);
    expect(latestOption().series[0].data).toEqual([0, null, 0.4, null, null]);
    expect(latestOption().series[1].data).toEqual([null, 0.2, null, 0.7, 1]);
  });

  it('formats ratios in a non-HTML tooltip while keeping untrusted model names as text', () => {
    const untrustedModel = '<img src=x onerror=alert(1)>';
    render(<AbilityChart rows={[{ model: untrustedModel, faithfulness: 0.498 }]} />);
    const option = latestOption();
    expect(option.series[0].name).toBe(untrustedModel);
    expect(option.tooltip.renderMode).toBe('richText');
    expect(option.tooltip.valueFormatter(0.498)).toBe('49.8%');
    expect(option.tooltip.valueFormatter(0)).toBe('0.0%');
    expect(option.tooltip.valueFormatter(null)).toBe('—');
    expect(document.querySelector('img')).toBeNull();
  });

  it('keeps risk metrics separate and uses the existing values', () => {
    const rows: ResultRow[] = fixture.result.summary_df;
    render(<RiskChart rows={rows} />);
    const option = latestOption();
    expect(option.xAxis.data).toEqual(['幻觉率', '拒答准确率']);
    option.series.forEach((series, index) => {
      expect(series.data).toEqual([rows[index].hallucination_rate, rows[index].refusal_accuracy]);
    });
    expect(option.tooltip.valueFormatter(0.6667)).toBe('66.7%');
  });

  it('uses per-model elapsed seconds without substituting missing durations', () => {
    const rows: ResultRow[] = [{ model: 'missing-duration', elapsed_seconds: null }, ...fixture.result.summary_df];
    render(<DurationChart rows={rows} />);
    const option = latestOption();
    expect(option.yAxis.data).toEqual(rows.map((row) => modelName(row.model)));
    expect(option.series[0].data.map((datum) => typeof datum === 'object' && datum !== null ? datum.value : datum)).toEqual(rows.map((row) => row.elapsed_seconds));
    expect(option.tooltip.renderMode).toBe('richText');
    expect(option.tooltip.valueFormatter(289.77)).toBe('289.8s');
    expect(option.tooltip.valueFormatter(null)).toBe('—');
  });

  it('resizes with ResizeObserver and disconnects and disposes on unmount', () => {
    const { unmount } = render(<AbilityChart rows={fixture.result.summary_df} />);
    const observer = resizeObservers[0];
    expect(observer.observe).toHaveBeenCalledWith(screen.getByRole('img'));
    act(() => observer.trigger());
    expect(chart.resize).toHaveBeenCalledTimes(1);
    unmount();
    expect(observer.disconnect).toHaveBeenCalledTimes(1);
    expect(chart.dispose).toHaveBeenCalledTimes(1);
    act(() => observer.trigger());
    expect(chart.resize).toHaveBeenCalledTimes(1);
  });

  it('falls back to window resizing and removes the listener on unmount', () => {
    vi.stubGlobal('ResizeObserver', undefined);
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const { unmount } = render(<RiskChart rows={fixture.result.summary_df} />);
    const handler = add.mock.calls.find(([event]) => event === 'resize')?.[1];
    expect(handler).toBeDefined();
    act(() => window.dispatchEvent(new Event('resize')));
    expect(chart.resize).toHaveBeenCalledTimes(1);
    unmount();
    expect(remove).toHaveBeenCalledWith('resize', handler);
    expect(chart.dispose).toHaveBeenCalledTimes(1);
    act(() => window.dispatchEvent(new Event('resize')));
    expect(chart.resize).toHaveBeenCalledTimes(1);
  });

  it('shows a neutral fallback if chart initialization fails', () => {
    vi.mocked(echarts.init).mockImplementation(() => { throw new Error('renderer unavailable'); });
    render(<AbilityChart rows={fixture.result.summary_df} />);
    expect(screen.getByRole('status')).toHaveTextContent('图表暂时无法显示');
    expect(resizeObservers).toHaveLength(0);
  });

  it('disposes a partially initialized chart when applying its options fails', () => {
    chart.setOption.mockImplementation(() => { throw new Error('invalid renderer state'); });
    render(<DurationChart rows={fixture.result.summary_df} />);
    expect(screen.getByRole('status')).toHaveTextContent('图表暂时无法显示');
    expect(chart.dispose).toHaveBeenCalledTimes(1);
    expect(resizeObservers).toHaveLength(0);
  });

  it('provides an unavailable state when every value is missing', () => {
    render(<AbilityChart rows={[{ model: 'deepseek-chat', faithfulness: null }]} />);
    expect(screen.getByRole('status')).toHaveTextContent('能力指标暂不可用');
    expect(echarts.init).not.toHaveBeenCalled();
  });
});

describe('comparison metrics table', () => {
  it('explains unavailable values without inferring a cause or changing zero and best-value titles', () => {
    const result: EvaluationResult = { ...fixture.result, summary_df: [
      { model: 'zero', faithfulness: 0, neg_context_precision: 0, neg_faithfulness: null },
      { model: 'best', faithfulness: 0.8, neg_context_precision: null },
      { model: 'invalid-number', faithfulness: 'not-a-number' },
    ] };
    render(<ComparisonView result={result} />);
    const table = screen.getByRole('table', { name: /各模型的全部原始指标/ });
    const explanation = '本次结果没有该项可显示值；可能未计算、无适用样本或返回空值，具体原因未区分。';
    expect(table.querySelector('caption')).toHaveTextContent('— 表示本次结果无此值，不等于 0');
    expect(document.querySelector('.comparison-table-panel .comparison-source-note')).toHaveTextContent('— 表示本次结果无此值，不等于 0');
    expect(table.querySelectorAll('td.comparison-missing span')).toHaveLength(6);
    table.querySelectorAll('td.comparison-missing span').forEach((cell) => {
      expect(cell).toHaveTextContent(/^—$/);
      expect(cell).toHaveAttribute('title', explanation);
    });
    const zeroRow = within(table).getByRole('rowheader', { name: 'zero' }).closest('tr')!;
    const zeroCells = within(zeroRow).getAllByRole('cell');
    expect(zeroCells[0]).toHaveTextContent('0.0%');
    expect(zeroCells[1]).toHaveTextContent('0.0%');
    expect(zeroCells[0].querySelector('span')).toHaveAttribute('title', '0.0%');
    expect(zeroCells[1].querySelector('span')).toHaveAttribute('title', '0.0%');
    expect(zeroCells[0]).not.toHaveClass('comparison-missing');
    expect(zeroCells[1]).not.toHaveClass('comparison-missing');
    expect(within(table).getByTitle('80.0%（本列最佳）')).toHaveTextContent('80.0%');
    expect(screen.queryByText(explanation)).not.toBeInTheDocument();
  });

  it('gives heat-matrix empty cells the same explanation while preserving zero and normal titles', () => {
    render(<HeatMatrix rows={[
      { model: 'zero', faithfulness: 0, context_precision: null },
      { model: 'complete', faithfulness: 0.8, context_precision: 0.6 },
      { model: 'absent' },
    ]} />);
    const table = screen.getByRole('table', { name: /各模型比率指标矩阵/ });
    const missingCells = table.querySelectorAll('td.is-missing');
    expect(missingCells).toHaveLength(3);
    missingCells.forEach((cell) => {
      expect(cell).toHaveTextContent(/^—$/);
      expect(cell).toHaveAttribute('title', '本次结果没有该项可显示值；可能未计算、无适用样本或返回空值，具体原因未区分。');
      expect(cell).not.toHaveAttribute('style');
    });
    const zero = within(table).getByTitle('zero 忠实度 0%');
    expect(zero).toHaveTextContent('0%');
    expect(zero).not.toHaveClass('is-missing');
    expect(zero).toHaveAttribute('style', '--heat: 0.000;');
    expect(within(table).getByTitle('complete 忠实度 80%')).toHaveTextContent('80%');
  });

  it('keeps all original and trace fields in a semantic table with original row order', () => {
    render(<ComparisonView result={fixture.result} />);
    const table = screen.getByRole('table', { name: /各模型的全部原始指标/ });
    const expectedKeys = Object.keys(fixture.result.summary_df[0]);
    expect(within(table).getAllByRole('columnheader')).toHaveLength(expectedKeys.length);
    expectedKeys.filter((key) => key !== 'model').forEach((key) => {
      expect(table.querySelector(`thead th[title="${key}"]`)).not.toBeNull();
    });
    expect(within(table).getAllByRole('rowheader').map((header) => header.querySelector('span')?.textContent)).toEqual(fixture.result.summary_df.map((row) => modelName(row.model)));
    expect(screen.getByRole('region', { name: '完整模型指标表，可横向滚动' })).toHaveClass('comparison-table-scroll');
    expect(within(table).getAllByRole('rowheader').every((header) => header.classList.contains('comparison-model-cell'))).toBe(true);
    expect(within(table).getByRole('rowheader', { name: /DeepSeek-V4\.1-Flash/ }).querySelector('small')?.textContent).toBe('请求名 deepseek-chat');
  });

  it('preserves later-row extension fields and displays missing values as unavailable', () => {
    const result: EvaluationResult = { ...fixture.result, summary_df: [{ model: 'first', faithfulness: null }, { model: 'second', faithfulness: 0, custom_evidence_count: 2 }] };
    render(<ComparisonView result={result} />);
    expect(screen.getByRole('columnheader', { name: /custom_evidence_count\s*条/ })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /各模型的全部原始指标/ })).toHaveTextContent('0.0%');
    expect(screen.getByRole('table', { name: /各模型的全部原始指标/ })).toHaveTextContent('—');
  });
});
