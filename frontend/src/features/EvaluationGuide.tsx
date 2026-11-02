// Adapted from ErgalicsStudio TourGuide.tsx / tourStore.ts, commit 86b7dbb5 (MIT).
// Copyright (c) 2026 SnowLeopard-io. See licenses/LICENSE-ErgalicsStudio-tour.txt.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../components/Icon';
import type { LabView } from '../labRoute';
import { BYOK } from '../config';
import './EvaluationGuide.css';

const STORAGE_KEY = 'veriscope:evaluation-guide';
interface Step { title: string; body: string; view?: LabView; selector?: string; needsResult?: boolean }
const STEPS: Step[] = [
  { title: '跟着做完一次评测', body: '先准备资料和题目，再选回答模型与打分裁判，最后查看证据并保存结果。教程只带你认位置，实际操作由你点击完成。' },
  { title: '从「新建评测」进入', body: '点击这里打开评测工作区，已有配置和结果仍会保留。接下来先准备模型可以依据的资料。', view: 'overview', selector: '[data-evaluation-guide="new"]' },
  { title: '上传或选择资料', body: '在数据区点「电脑文件」导入自己的资料，也可直接选项目示例。检索到的资料会提供给模型回答和裁判核对。', view: 'workspace', selector: '[data-evaluation-guide="document"]' },
  { title: '准备评测题目', body: '没有题集时保留「基于文档自动生成」，开始后会生成问题和参考答案。已有题目则选「已有自定义测试集」，导入 CSV 或 JSON 题集。', view: 'workspace', selector: '[data-evaluation-guide="testset"]' },
  { title: '选择谁来回答', body: BYOK ? '被测模型负责根据资料回答同一组问题，可以选择一个或多个。先在「模型配置」填写自己的接口与 API Key，再回到这里选择。' : '被测模型负责根据资料回答同一组问题，可以选择一个或多个。使用自己的模型时，先在「模型配置」登记，并在本地后端配置密钥。', view: 'workspace', selector: '[data-evaluation-guide="models"]' },
  { title: '选择谁来打分', body: '裁判负责五项指标和逐句核对；不单独选择时，各被测模型也给自己的回答打分。要统一交给另一个模型打分，在下拉框中选择该裁判。', view: 'workspace', selector: '[data-evaluation-guide="judge"]' },
  { title: '确认质量门禁阈值', body: '通常可保留「沿用后端默认阈值」，实际阈值随结果显示。门禁检查正向题的忠实度与上下文精度，两项都达标才通过。', view: 'workspace', selector: '[data-evaluation-guide="thresholds"]' },
  { title: '开始评测并等待', body: '准备好后点击「开始评测」，这时才会调用模型并可能产生费用。运行中显示等待条，完成后自动返回总览；等待时保持页面打开。', view: 'workspace', selector: '[data-evaluation-guide="run"]' },
  { title: '先读总览与门禁', body: '总览先看结果来源、耗时和门禁，再比较各模型指标。这里会注明当前是本次结果、历史记录还是演示快照。', view: 'overview', selector: '[data-evaluation-guide="result"] .gate-summary, [data-evaluation-guide="result"] .initial-state', needsResult: true },
  { title: '逐句查看证据', body: '进入「幻觉分析」，选择模型与问题，查看回答中各句的标签和检索证据。发现可疑处时，对照原文逐项核对。', view: 'trace', selector: '[data-evaluation-guide="trace"] .trace-toolbar', needsResult: true },
  { title: '查看完整评测报告', body: '「评测报告」可读取当前记录保存的 HTML 报告，也能手动导入本地报告预览。若只有产物路径，可先从本地找到报告再导入。', view: 'report', selector: '[data-evaluation-guide="report"] .report-toolbar', needsResult: true },
  { title: '把结果保存到电脑', body: '评测完成后，后端会自动尝试保存完整记录；点「保存记录」将 JSON 下载到电脑。下载后查看提示，确认是否包含报告和图表。', view: 'report', selector: '[data-evaluation-guide="save"]', needsResult: true },
  { title: '历史记录与演示后返回', body: '「历史记录」可打开服务记录或导入备份，打开记录不会重新调用模型。「加载演示」只载入冻结快照；已有真实结果时，随后可点「返回评测结果」继续查看。', view: 'overview', selector: '[data-evaluation-guide="history"]' },
];

interface Rect { top: number; left: number; width: number; height: number }
interface Props {
  view: LabView;
  onNavigate: (view: LabView) => void;
  hasResult: boolean;
  isDemo: boolean;
  busy: boolean;
  readOnly: boolean;
  staticSite: boolean;
  allowWelcome: boolean;
}

function unseen() {
  try { return localStorage.getItem(STORAGE_KEY) !== 'done'; } catch { return false; }
}

/** Prefer a side with enough space; a short viewport gets a scrollable card beside the target. */
function placeTip(rect: Rect, width: number, height: number): CSSProperties {
  const margin = 16;
  const gap = 18;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const centeredLeft = Math.min(Math.max(margin, rect.left + rect.width / 2 - width / 2), viewportWidth - width - margin);
  const below = viewportHeight - rect.top - rect.height - gap - margin;
  const above = rect.top - gap - margin;
  if (below >= height) return { top: rect.top + rect.height + gap, left: centeredLeft, width };
  if (above >= height) return { top: rect.top - gap - height, left: centeredLeft, width };
  const top = Math.min(Math.max(margin, rect.top), Math.max(margin, viewportHeight - height - margin));
  if (viewportWidth - rect.left - rect.width >= width + gap + margin) return { top, left: rect.left + rect.width + gap, width };
  if (rect.left >= width + gap + margin) return { top, left: rect.left - width - gap, width };
  // A target can fill the viewport; keep navigation visible even when no outside placement fits.
  if (Math.max(below, above) < 80) return { top: margin, left: centeredLeft, width, maxHeight: viewportHeight - margin * 2 };
  return below >= above
    ? { top: rect.top + rect.height + gap, left: centeredLeft, width, maxHeight: Math.max(80, below) }
    : { top: margin, left: centeredLeft, width, maxHeight: Math.max(80, above) };
}

export function EvaluationGuide({ view, onNavigate, hasResult, isDemo, busy, readOnly, staticSite, allowWelcome }: Props) {
  // Eligibility is considered only on mounting: typing, an evaluation, or a later page change never opens a welcome.
  const [active, setActive] = useState(() => allowWelcome && !busy && !hasResult && unseen());
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [tipHeight, setTipHeight] = useState(270);
  const trigger = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const expectedView = useRef<LabView | null>(null);
  const previousView = useRef(view);
  const previousBusy = useRef(busy);
  const definition = STEPS[step];

  const finish = useCallback(() => {
    setActive(false);
    expectedView.current = null;
    try { localStorage.setItem(STORAGE_KEY, 'done'); } catch { /* Manual entry remains available. */ }
    trigger.current?.focus({ preventScroll: true });
  }, []);

  const move = (next: number) => {
    const destination = STEPS[next];
    if (!destination) return;
    setRect(null);
    setStep(next);
    if (destination.view && destination.view !== view) {
      expectedView.current = destination.view;
      onNavigate(destination.view);
    }
  };

  useEffect(() => {
    if (previousView.current !== view && active) {
      if (expectedView.current === view) expectedView.current = null;
      else finish();
    }
    previousView.current = view;
    if (previousBusy.current !== busy && active) finish();
    previousBusy.current = busy;
  }, [view, busy, active, finish]);

  useEffect(() => {
    if (!active) return;
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); finish(); } };
    const leave = () => finish();
    window.addEventListener('keydown', key);
    window.addEventListener('hashchange', leave);
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('hashchange', leave); };
  }, [active, finish]);

  // Upstream spotlight geometry, using layout events and observers instead of continuous polling.
  useLayoutEffect(() => {
    if (!active) return;
    const target = definition.selector ? document.querySelector<HTMLElement>(definition.selector) : null;
    target?.scrollIntoView?.({ block: 'center', behavior: 'instant' });
    tip.current?.focus({ preventScroll: true });
    let frame = 0;
    const update = () => {
      const element = definition.selector ? document.querySelector<HTMLElement>(definition.selector) : null;
      const box = element?.getBoundingClientRect();
      if (box) {
        const top = Math.max(box.top, 8);
        const left = Math.max(box.left, 8);
        const right = Math.min(box.right, window.innerWidth - 8);
        const bottom = Math.min(box.bottom, window.innerHeight - 8);
        setRect(right > left && bottom > top ? { top, left, width: right - left, height: bottom - top } : null);
      } else setRect(null);
      // Measure the natural content as well: a short-viewport max-height must not alternate on/off.
      const height = tip.current ? Math.max(tip.current.scrollHeight + 4, tip.current.getBoundingClientRect().height) : 0;
      if (height) setTipHeight(height);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(update); };
    update();
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    const resize = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null;
    if (target) resize?.observe(target);
    if (tip.current) resize?.observe(tip.current);
    const main = document.getElementById('main-content');
    const mutation = new MutationObserver(schedule);
    if (main) mutation.observe(main, { childList: true, subtree: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
      resize?.disconnect();
      mutation.disconnect();
    };
  }, [active, step, view, definition]);

  const width = Math.min(400, window.innerWidth - 32);
  const tipStyle = rect ? placeTip(rect, width, tipHeight) : { width: 'min(400px, calc(100vw - 32px))' };
  const resultMissing = definition.needsResult && !hasResult;
  const boundary = !(readOnly || staticSite) ? null : step === 0
    ? staticSite ? '当前是静态演示站，无后端；上传、评测和记录操作需在本地运行。' : '当前是公开只读站；上传、评测和记录操作需在本地运行。'
    : ({ 2: '当前站点不能上传文件，可选择示例资料了解流程。', 7: '当前站点不能启动评测，请在本地运行。', 11: '当前站点不能下载评测记录，请在本地保存。', 12: '当前站点不能读取或导入历史记录；可点「加载演示」查看快照。' } as Record<number, string>)[step];

  return <>
    <button ref={trigger} type="button" className="btn btn-ghost evaluation-guide-trigger" aria-haspopup="dialog" aria-expanded={active} onClick={() => { expectedView.current = null; setStep(0); setActive(true); }}><Icon name="play" size={14}/>使用教程</button>
    {active && createPortal(<div className="evaluation-guide-layer" role="dialog" aria-modal="false" aria-label="评测使用教程" aria-describedby="evaluation-guide-body">
      {rect ? <div className="evaluation-guide-spot" aria-hidden="true" data-guide-highlight={definition.selector} style={{ top: rect.top - 5, left: rect.left - 5, width: rect.width + 10, height: rect.height + 10 }}/> : <div className="evaluation-guide-backdrop" aria-hidden="true"/>}
      <div ref={tip} tabIndex={-1} className={`evaluation-guide-tip${rect ? '' : ' evaluation-guide-tip-center'}`} style={tipStyle}>
        <div className="evaluation-guide-eyebrow"><span>使用教程</span><span>{step + 1} / {STEPS.length}</span><button type="button" className="evaluation-guide-close" aria-label="关闭教程" onClick={finish}>×</button></div>
        <h2>{definition.title}</h2>
        <p id="evaluation-guide-body">{isDemo && step === 11 ? '当前是演示快照，不能保存评测记录。完成自己的评测后，再从这里下载 JSON 备份。' : definition.body}</p>
        {step === 0 && <div className="evaluation-guide-flow" aria-label="评测流程"><span>资料</span><span>题目</span><span>模型与裁判</span><span>证据</span><span>保存</span></div>}
        {boundary && <p className="evaluation-guide-note">{boundary}</p>}
        {resultMissing ? <p className="evaluation-guide-note">当前还没有评测结果，先完成一次评测；本步只介绍查看位置。</p>
          : !rect && definition.selector && <p className="evaluation-guide-note">目标区域当前不在可见范围；可滚动页面查看，或继续了解下一步。</p>}
        <div className="evaluation-guide-dots" aria-hidden="true">{STEPS.map((_, index) => <span key={index} className={index === step ? 'is-active' : ''}/>)}</div>
        <div className="evaluation-guide-actions"><button type="button" className="btn btn-ghost" onClick={finish}>跳过</button><div><button type="button" className="btn btn-secondary" disabled={step === 0} onClick={() => move(step - 1)}>上一步</button><button type="button" className="btn btn-primary" onClick={() => step === STEPS.length - 1 ? finish() : move(step + 1)}>{step === STEPS.length - 1 ? '完成教程' : step === 0 ? '开始了解' : '下一步'}</button></div></div>
      </div>
    </div>, document.body)}
  </>;
}
