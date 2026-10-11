import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import type { ComposeOption } from 'echarts/core';
import { BarChart } from 'echarts/charts';
import type { BarSeriesOption } from 'echarts/charts';
import { AriaComponent, AxisPointerComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import type { AriaComponentOption, AxisPointerComponentOption, GridComponentOption, LegendComponentOption, TooltipComponentOption } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { ResultRow } from '../api/types';
import { METRICS } from '../config';
import { modelName, numberValue, percent, seconds } from '../utils/format';
import { Icon } from './Icon';

echarts.use([BarChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, AxisPointerComponent, MarkLineComponent, SVGRenderer]);

type EChartsOption = ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption | LegendComponentOption | AriaComponentOption | AxisPointerComponentOption>;

const MODEL_COLORS = ['#59c3d1', '#9297ed', '#cba575', '#8fb8d1', '#bd98b4', '#b0c391'];
const FONT_FAMILY = 'Inter, "PingFang SC", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif';
const AXIS_COLOR = '#a7b0be';
const GRID_COLOR = 'rgba(255, 255, 255, 0.08)';

/** Compact chart labels: whole percentages drop the trailing ".0" so side-by-side bars never overlap. */
function ratioLabel(value: unknown): string {
  const number = numberValue(value);
  if (number === null) return '—';
  const scaled = Math.round(number * 1000) / 10;
  return `${Number.isInteger(scaled) ? scaled.toFixed(0) : scaled.toFixed(1)}%`;
}

function chartTheme() {
  const styles = typeof document === 'undefined' ? undefined : getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => styles?.getPropertyValue(name).trim() || fallback;
  return {
    fontFamily: token('--font-family', FONT_FAMILY),
    labelSize: Number.parseFloat(token('--font-size-label', '13')) || 13,
    captionSize: Number.parseFloat(token('--font-size-caption', '12')) || 12,
    text: token('--chart-text', '#f5f7fa'),
    axis: token('--chart-axis', AXIS_COLOR),
    grid: token('--chart-grid', GRID_COLOR),
    background: token('--chart-background', 'transparent'),
    tooltipBackground: token('--chart-tooltip-bg', '#1b2430'),
    tooltipBorder: token('--chart-tooltip-border', 'rgba(255, 255, 255, 0.16)'),
    tooltipText: token('--chart-tooltip-text', '#f5f7fa'),
    pointer: token('--chart-pointer', 'rgba(255, 255, 255, 0.04)'),
  };
}

/** 门禁门槛参考线：只在门禁实际检查的两个指标（忠实度、上下文精度）上绘制，
 *  不做成全宽横线，避免把"整体均值"误读成"门禁判定量"。 */
function gateMarkLine(labels: string[], gateLine: readonly [number, number], theme: ReturnType<typeof chartTheme>): BarSeriesOption['markLine'] {
  return {
    silent: true,
    symbol: 'none',
    animation: false,
    lineStyle: { color: 'rgba(255, 255, 255, 0.5)', type: 'dashed', width: 1.5 },
    label: {
      show: true,
      position: 'insideEndTop',
      formatter: '门禁门槛',
      color: theme.axis,
      fontFamily: theme.fontFamily,
      fontSize: theme.captionSize,
    },
    data: [[{ coord: [labels[0], gateLine[0]] }, { coord: [labels[1], gateLine[1]] }]],
  };
}

function modelSeries(rows: ResultRow[], keys: readonly string[], showValues: boolean, theme: ReturnType<typeof chartTheme>): BarSeriesOption[] {
  return rows.map((row, index) => ({
    name: modelName(row.model),
    type: 'bar',
    data: keys.map((key) => numberValue(row[key])),
    barMaxWidth: 34,
    barGap: '25%',
    itemStyle: { color: MODEL_COLORS[index % MODEL_COLORS.length], borderRadius: [3, 3, 0, 0] },
    label: { show: showValues, position: 'top', color: theme.text, fontFamily: theme.fontFamily, fontSize: theme.captionSize, formatter: (params) => ratioLabel(params.value) },
    emphasis: { focus: 'series' },
  }));
}

function ratioOption(rows: ResultRow[], labels: string[], keys: readonly string[], description: string, showValues = false, gateLine?: readonly [number, number]): EChartsOption {
  const theme = chartTheme();
  const series = modelSeries(rows, keys, showValues, theme);
  // 只在第一条系列上挂门槛线，避免多系列重复绘制同一条线。
  if (gateLine && series.length > 0) series[0].markLine = gateMarkLine(labels, gateLine, theme);
  return {
    animation: false,
    backgroundColor: theme.background,
    color: MODEL_COLORS,
    textStyle: { fontFamily: theme.fontFamily, color: theme.axis },
    aria: { enabled: true, description, decal: { show: false } },
    legend: {
      top: 8,
      left: 'center',
      type: 'scroll',
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 8,
      itemGap: 22,
      textStyle: { color: theme.text, fontFamily: theme.fontFamily, fontSize: theme.labelSize },
      pageTextStyle: { color: theme.axis },
      pageIconColor: theme.axis,
      pageIconInactiveColor: theme.grid,
    },
    grid: { top: 64, right: 22, bottom: 22, left: 18, containLabel: true },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      confine: true,
      backgroundColor: theme.tooltipBackground,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontFamily: theme.fontFamily, fontSize: theme.labelSize },
      axisPointer: { type: 'shadow', shadowStyle: { color: theme.pointer } },
      valueFormatter: (value) => percent(value),
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { show: false },
      axisLabel: { color: theme.axis, fontFamily: theme.fontFamily, fontSize: theme.captionSize, margin: 16, interval: 0, rotate: 0 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      interval: 0.25,
      axisLabel: { color: theme.axis, fontFamily: theme.fontFamily, fontSize: theme.captionSize, formatter: (value: number) => `${Math.round(value * 100)}%` },
      splitLine: { lineStyle: { color: theme.grid, type: 'dashed' } },
    },
    series,
    media: [
      { query: { maxWidth: 380 }, option: { series: rows.map(() => ({ type: 'bar', label: { show: false } })) } },
      { query: { maxWidth: 540 }, option: { xAxis: { axisLabel: { fontSize: theme.captionSize } }, grid: { bottom: 32, right: 8 } } },
      { option: { series: rows.map(() => ({ type: 'bar', label: { show: showValues } })), xAxis: { axisLabel: { fontSize: theme.captionSize, rotate: 0 } }, grid: { bottom: 22, right: 22 } } },
    ],
  };
}

function ChartCanvas({ option, label, height }: { option: EChartsOption; label: string; height: number }) {
  const element = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const host = element.current;
    if (!host) return;

    setFailed(false);
    let chart: ReturnType<typeof echarts.init> | undefined;
    try {
      chart = echarts.init(host, undefined, { renderer: 'svg' });
      chart.setOption(option, { notMerge: true });
    } catch {
      chart?.dispose();
      setFailed(true);
      return;
    }

    const activeChart = chart;
    let active = true;
    const resize = () => { if (active) activeChart.resize(); };
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
    if (observer) observer.observe(host);
    else window.addEventListener('resize', resize);

    return () => {
      active = false;
      observer?.disconnect();
      if (!observer) window.removeEventListener('resize', resize);
      activeChart.dispose();
    };
  }, [option]);

  return <div className="evaluation-chart" style={{ height }}>
    <div ref={element} className="evaluation-chart-canvas" role="img" aria-label={label} style={{ height }} />
    {failed && <div className="evaluation-chart-fallback" role="status"><Icon name="alert" size={22} /><span>图表暂时无法显示</span></div>}
  </div>;
}

function UnavailableChart({ title, height }: { title: string; height: number }) {
  return <div className="evaluation-chart-unavailable" style={{ minHeight: height }} role="status">
    <Icon name="chart" size={26} />
    <strong>{title}</strong>
    <span>评测结果未提供这些指标，缺失值不会显示为 0。</span>
  </div>;
}

export function AbilityChart({ rows, height = 500, gateLine }: { rows: ResultRow[]; height?: number; gateLine?: readonly [number, number] }) {
  const keys = METRICS.map((metric) => metric.key);
  const base = `五项模型能力对比：${METRICS.map((metric) => metric.label).join('、')}。按评测返回顺序展示，百分比越高越好。缺失指标不绘制。`;
  const description = gateLine
    ? `${base}虚线为门禁门槛，仅画在门禁实际检查的忠实度与上下文精度两项上。`
    : base;
  const option = useMemo(() => ratioOption(rows, METRICS.map((metric) => metric.label), METRICS.map((metric) => metric.key), description, true, gateLine), [rows, description, gateLine]);
  const available = rows.some((row) => keys.some((key) => numberValue(row[key]) !== null));
  if (!available) return <UnavailableChart title="能力指标暂不可用" height={height} />;
  return <ChartCanvas option={option} label={description} height={height} />;
}

export function RiskChart({ rows }: { rows: ResultRow[] }) {
  const description = '风险指标对比：幻觉率越低越好，拒答准确率越高越好。分别展示两项原始指标，缺失指标不绘制。';
  const keys = ['hallucination_rate', 'refusal_accuracy'];
  const option = useMemo(() => ratioOption(rows, ['幻觉率', '拒答准确率'], ['hallucination_rate', 'refusal_accuracy'], description, true), [rows]);
  const available = rows.some((row) => keys.some((key) => numberValue(row[key]) !== null));
  if (!available) return <UnavailableChart title="风险指标暂不可用" height={320} />;
  return <ChartCanvas option={option} label={description} height={320} />;
}

export function DurationChart({ rows }: { rows: ResultRow[] }) {
  const description = '各模型本次评测耗时，单位为秒。按评测返回顺序展示，缺失耗时不绘制。';
  const option = useMemo<EChartsOption>(() => {
    const theme = chartTheme();
    return {
      animation: false,
      backgroundColor: theme.background,
      color: MODEL_COLORS,
      textStyle: { color: theme.axis, fontFamily: theme.fontFamily },
      aria: { enabled: true, description, decal: { show: false } },
      grid: { top: 12, right: 64, bottom: 40, left: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        renderMode: 'richText',
        confine: true,
        backgroundColor: theme.tooltipBackground,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontFamily: theme.fontFamily, fontSize: theme.labelSize },
        axisPointer: { type: 'shadow', shadowStyle: { color: theme.pointer } },
        valueFormatter: (value) => seconds(value),
      },
      xAxis: {
        type: 'value',
        name: '评测耗时（秒）',
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: theme.axis, fontFamily: theme.fontFamily, fontSize: theme.captionSize },
        axisLine: { lineStyle: { color: theme.grid } },
        axisTick: { show: false },
        axisLabel: { color: theme.axis, fontFamily: theme.fontFamily, fontSize: theme.captionSize },
        splitLine: { lineStyle: { color: theme.grid, type: 'dashed' } },
      },
      yAxis: {
        type: 'category',
        data: rows.map((row) => modelName(row.model)),
        inverse: true,
        axisLine: { lineStyle: { color: theme.grid } },
        axisTick: { show: false },
        axisLabel: { color: theme.axis, fontFamily: theme.fontFamily, fontSize: theme.captionSize, margin: 12, interval: 0 },
      },
      series: [{
        name: '评测耗时',
        type: 'bar',
        barMaxWidth: 30,
        data: rows.map((row, index) => ({
          value: numberValue(row.elapsed_seconds),
          itemStyle: { color: MODEL_COLORS[index % MODEL_COLORS.length], borderRadius: [0, 3, 3, 0] },
        })),
        label: {
          show: true,
          position: 'right',
          color: theme.text,
          fontFamily: theme.fontFamily,
          fontSize: theme.captionSize,
          formatter: (params) => seconds(params.value),
        },
        emphasis: { focus: 'series' },
      }],
    };
  }, [rows]);
  if (!rows.some((row) => numberValue(row.elapsed_seconds) !== null)) return <UnavailableChart title="耗时数据暂不可用" height={320} />;
  return <ChartCanvas option={option} label={description} height={320} />;
}
