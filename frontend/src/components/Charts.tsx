import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import type { ComposeOption } from 'echarts/core';
import { BarChart } from 'echarts/charts';
import type { BarSeriesOption } from 'echarts/charts';
import { AriaComponent, AxisPointerComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import type { AriaComponentOption, AxisPointerComponentOption, GridComponentOption, LegendComponentOption, TooltipComponentOption } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { ResultRow } from '../api/types';
import { METRICS } from '../config';
import { modelName, numberValue, percent, seconds } from '../utils/format';
import { Icon } from './Icon';

echarts.use([BarChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, AxisPointerComponent, SVGRenderer]);

type EChartsOption = ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption | LegendComponentOption | AriaComponentOption | AxisPointerComponentOption>;

const MODEL_COLORS = ['#59c3d1', '#9297ed', '#cba575', '#8fb8d1', '#bd98b4', '#b0c391'];
const FONT_FAMILY = 'Inter, "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif';
const AXIS_COLOR = '#a5b1c0';
const GRID_COLOR = 'rgba(165, 177, 192, 0.12)';

function chartTheme() {
  const styles = typeof document === 'undefined' ? undefined : getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => styles?.getPropertyValue(name).trim() || fallback;
  return {
    axis: token('--chart-axis', AXIS_COLOR),
    grid: token('--chart-grid', GRID_COLOR),
    background: token('--chart-background', 'transparent'),
    tooltipBackground: token('--chart-tooltip-bg', '#10161f'),
    tooltipBorder: token('--chart-tooltip-border', '#3c4b5b'),
    tooltipText: token('--chart-tooltip-text', '#edf2f7'),
    pointer: token('--chart-pointer', 'rgba(89, 195, 209, 0.06)'),
  };
}

function modelSeries(rows: ResultRow[], keys: readonly string[], showValues: boolean): BarSeriesOption[] {
  return rows.map((row, index) => ({
    name: modelName(row.model),
    type: 'bar',
    data: keys.map((key) => numberValue(row[key])),
    barMaxWidth: 34,
    barGap: '25%',
    itemStyle: { color: MODEL_COLORS[index % MODEL_COLORS.length], borderRadius: [3, 3, 0, 0] },
    label: { show: showValues, position: 'top', color: '#edf2f7', fontFamily: FONT_FAMILY, fontSize: 12, formatter: (params) => percent(params.value) },
    emphasis: { focus: 'series' },
  }));
}

function ratioOption(rows: ResultRow[], labels: string[], keys: readonly string[], description: string, showValues = false): EChartsOption {
  const theme = chartTheme();
  return {
    animation: false,
    backgroundColor: theme.background,
    color: MODEL_COLORS,
    textStyle: { fontFamily: FONT_FAMILY, color: theme.axis },
    aria: { enabled: true, description, decal: { show: false } },
    legend: {
      top: 8,
      left: 'center',
      type: 'scroll',
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 8,
      itemGap: 22,
      textStyle: { color: '#edf2f7', fontFamily: FONT_FAMILY, fontSize: 13 },
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
      textStyle: { color: theme.tooltipText, fontFamily: FONT_FAMILY, fontSize: 13 },
      axisPointer: { type: 'shadow', shadowStyle: { color: theme.pointer } },
      valueFormatter: (value) => percent(value),
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { show: false },
      axisLabel: { color: theme.axis, fontFamily: FONT_FAMILY, fontSize: 12, margin: 16, interval: 0, rotate: 0 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      interval: 0.25,
      axisLabel: { color: theme.axis, fontFamily: FONT_FAMILY, fontSize: 12, formatter: (value: number) => `${Math.round(value * 100)}%` },
      splitLine: { lineStyle: { color: theme.grid, type: 'dashed' } },
    },
    series: modelSeries(rows, keys, showValues),
    media: [
      { query: { maxWidth: 700 }, option: { series: rows.map(() => ({ type: 'bar', label: { show: false } })) } },
      { query: { maxWidth: 540 }, option: { xAxis: { axisLabel: { fontSize: 12, rotate: 18 } }, grid: { bottom: 32, right: 8 } } },
      { option: { series: rows.map(() => ({ type: 'bar', label: { show: showValues } })), xAxis: { axisLabel: { fontSize: 12, rotate: 0 } }, grid: { bottom: 22, right: 22 } } },
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

export function AbilityChart({ rows }: { rows: ResultRow[] }) {
  const keys = METRICS.map((metric) => metric.key);
  const description = `五项模型能力对比：${METRICS.map((metric) => metric.label).join('、')}。按评测返回顺序展示，百分比越高越好。缺失指标不绘制。`;
  const option = useMemo(() => ratioOption(rows, METRICS.map((metric) => metric.label), METRICS.map((metric) => metric.key), description, true), [rows, description]);
  const available = rows.some((row) => keys.some((key) => numberValue(row[key]) !== null));
  if (!available) return <UnavailableChart title="能力指标暂不可用" height={500} />;
  return <ChartCanvas option={option} label={description} height={500} />;
}

export function RiskChart({ rows }: { rows: ResultRow[] }) {
  const description = '风险指标对比：幻觉率越低越好，拒答准确率越高越好。分别展示两项原始指标，缺失指标不绘制。';
  const keys = ['hallucination_rate', 'refusal_accuracy'];
  const option = useMemo(() => ratioOption(rows, ['幻觉率', '拒答准确率'], ['hallucination_rate', 'refusal_accuracy'], description), [rows]);
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
    textStyle: { color: theme.axis, fontFamily: FONT_FAMILY },
    aria: { enabled: true, description, decal: { show: false } },
    legend: { top: 8, left: 'center', icon: 'roundRect', itemWidth: 12, itemHeight: 8, textStyle: { color: '#edf2f7', fontFamily: FONT_FAMILY, fontSize: 13 } },
    grid: { top: 64, right: 22, bottom: 22, left: 18, containLabel: true },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      confine: true,
      backgroundColor: theme.tooltipBackground,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontFamily: FONT_FAMILY, fontSize: 13 },
      axisPointer: { type: 'shadow', shadowStyle: { color: theme.pointer } },
      valueFormatter: (value) => seconds(value),
    },
    xAxis: { type: 'category', data: rows.map((row) => modelName(row.model)), axisLine: { lineStyle: { color: theme.grid } }, axisTick: { show: false }, axisLabel: { color: theme.axis, fontFamily: FONT_FAMILY, fontSize: 12, margin: 16, interval: 0 } },
    yAxis: { type: 'value', min: 0, name: '秒', nameTextStyle: { color: theme.axis, fontFamily: FONT_FAMILY, fontSize: 12 }, axisLabel: { color: theme.axis, fontFamily: FONT_FAMILY, fontSize: 12 }, splitLine: { lineStyle: { color: theme.grid, type: 'dashed' } } },
    series: [{ name: '评测耗时', type: 'bar', barMaxWidth: 42, data: rows.map((row, index) => ({ value: numberValue(row.elapsed_seconds), itemStyle: { color: MODEL_COLORS[index % MODEL_COLORS.length], borderRadius: [3, 3, 0, 0] } })) }],
    media: [{ query: { maxWidth: 540 }, option: { xAxis: { axisLabel: { fontSize: 12, rotate: 18 } }, grid: { bottom: 32, right: 8 } } }],
    };
  }, [rows]);
  if (!rows.some((row) => numberValue(row.elapsed_seconds) !== null)) return <UnavailableChart title="耗时数据暂不可用" height={320} />;
  return <ChartCanvas option={option} label={description} height={320} />;
}
