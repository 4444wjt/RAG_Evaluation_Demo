import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { checkHealth, ConnectionError, evaluate, isEvaluationResult } from './api/client';
import type { DemoFixture, EvaluationRecord, EvaluationResult } from './api/types';
import { downloadEvaluationRecord, downloadRecordJson } from './api/evaluations';
import { BrandMark } from './components/BrandMark';
import { Icon } from './components/Icon';
import type { IconName } from './components/Icon';
import { OverviewView } from './features/OverviewView';
import { WorkspaceView } from './features/WorkspaceView';
import type { ConfigurationCheck } from './features/WorkspaceView';
import type { ExampleEvidence } from './features/SampleDocumentGuide';
import { ComparisonView } from './features/ComparisonView';
import { TraceView } from './features/TraceView';
import { ReportView } from './features/ReportView';
import { EvaluationHistory } from './features/EvaluationHistory';
import { EvaluationGuide } from './features/EvaluationGuide';
import { EvaluationVerificationView } from './features/EvaluationVerificationView';
import { GateAuditView } from './features/GateAuditView';
import { FixVerificationView } from './features/FixVerificationView';
import { RobustnessView } from './features/RobustnessView';
import { BlindspotView } from './features/BlindspotView';
import { AnnotateView } from './features/AnnotateView';
import { ParentContextView } from './features/ParentContextView';
import { CitationVersionView } from './features/CitationVersionView';
import { KnowledgeUpdateView } from './features/KnowledgeUpdateView';
import { ModelsView } from './features/ModelsView';
import { fetchModels } from './api/models';
import type { ModelEntry } from './api/models';
import { byokEntries } from './api/byok';
import demoData from './fixtures/demo.json';
import sourceRegistry from './fixtures/source-registry.json';
import { BYOK, MODEL_COLORS, MODELS, READ_ONLY, REPO_URL, STATIC_SITE } from './config';
import { labHash, parseLabHash } from './labRoute';
import type { LabView } from './labRoute';
import { fromRequest, initialConfig, initialRunState, runReducer, toRequest, validateConfig } from './state';
import { modelName, seconds } from './utils/format';
import type { EntryRequest } from './AppEntry';

const demo = demoData as DemoFixture;
const demoRun = demo.provenance.run as { version?: unknown; doc_name?: unknown } | undefined;
const demoVersion = typeof demoRun?.version === 'string' ? demoRun.version : demo.provenance.label;
const demoSource = sourceRegistry.documents.find((entry) => entry.path === demoRun?.doc_name);
// Only the frozen demo is bound to the registered source; live API results have no document hash.
const demoDocument = demoSource ? { path: demoSource.path, sha256: demoSource.sha256 } : null;
type DashboardView = LabView;
const navigation: { id: DashboardView; label: string; icon: IconName; title: string; description: string }[] = [
  { id: 'overview', label: '评测总览', icon: 'overview', title: '评测总览', description: '从质量门禁到逐句证据，检查模型回答的可靠性。' },
  { id: 'workspace', label: '评测工作区', icon: 'layers', title: '评测工作区', description: '选择数据和模型，运行一次可解释的可靠性评测。' },
  { id: 'models', label: '模型配置', icon: 'sliders', title: '模型配置', description: BYOK ? '云端使用自己的模型；填写接口和 API Key，配置仅留在当前页面内存。' : '登记自己的 OpenAI 兼容模型；密钥留在本机 .env，页面只显示是否已设置。' },
  { id: 'comparison', label: '多模型对比', icon: 'chart', title: '多模型对比', description: '同一测试集上的指标与耗时，保留原有模型顺序。' },
  { id: 'trace', label: '幻觉分析', icon: 'trace', title: '幻觉分析', description: '阅读模型回答，逐句检查事实判断与检索证据。' },
  { id: 'report', label: '评测报告', icon: 'file', title: '评测报告', description: '查看已生成的 HTML 报告，保留原始评测内容。' },
  { id: 'verification', label: '评测验证', icon: 'check', title: '评测验证', description: '查看人工核验状态与原始依据。' },
  { id: 'audit', label: '门禁审计', icon: 'shield', title: '门禁审计', description: '检验门禁本身：结论能否复算、换裁判会不会变、有多确定，以及门禁代码对不对。' },
  { id: 'fix', label: '修复验证', icon: 'wrench', title: '修复验证', description: '门禁拦下之后：回查原文、跟踪提示词调整后的同题重跑，再看留出题对照。逐题核对改善与未解决项。' },
  { id: 'robustness', label: '鲁棒性体检', icon: 'pulse', title: '鲁棒性体检', description: '打乱、加噪、移除依据、改成反事实：看被测应用和逐句核对是否真的依据资料。' },
  { id: 'blindspot', label: '盲区体检', icon: 'layers', title: '盲区体检', description: '从发现引用盲区，到补标签处理、核对归档复测，再导出复核记录。' },
  { id: 'annotate', label: '人工标注', icon: 'pen', title: '人工标注', description: '请非作者逐条判断门禁所用的陈述能否由资料推出，用来估计裁判判错的程度。' },
  { id: 'parent-context', label: '父条资料准备', icon: 'folder', title: '父条资料准备', description: '导入原文、段快照、检索命中和绑定声明，把命中段补全为完整父条，复核后在本机下载。不调用模型，不作门禁判定。' },
  { id: 'citation-version', label: '引用版本检查', icon: 'file', title: '引用版本检查', description: '上传英国官方 CLML XML，核对版本字段是否满足日期、地域与语言要求。只认原件明示字段：其他格式没有可比对的版本元数据，不在范围内。' },
  { id: 'knowledge-update', label: '知识更新', icon: 'file', title: '知识更新', description: '比较固定来源版本，定位条目引文，人工修订后复核并下载。不判断语义，不批准发布。' },
];

/** Navigation groups: the evaluation itself, the checks on it, then material preparation. The top tabs choose which
 *  group the sidebar shows; on narrow screens every group stays in the sidebar. Each group keeps its own tone. */
const NAV_GROUPS: { id: string; label: string; tone: 'eval' | 'audit' | 'prep'; views: DashboardView[] }[] = [
  { id: 'nav-group-eval', label: '评测', tone: 'eval', views: ['overview', 'workspace', 'models', 'comparison', 'trace', 'report'] },
  { id: 'nav-group-audit', label: '验证与审计', tone: 'audit', views: ['verification', 'audit', 'fix', 'robustness', 'blindspot', 'annotate'] },
  { id: 'nav-group-prep', label: '资料准备', tone: 'prep', views: ['parent-context', 'citation-version', 'knowledge-update'] },
];
const groupOf = (target: DashboardView) => (NAV_GROUPS.find((group) => group.views.includes(target)) ?? NAV_GROUPS[0]).id;

/** Sidebar context card. Labels carry their own prefix ("门禁 PASS", "来源 · …") so they never duplicate a field shown in the main view. */
function SidebarRun({ result, source }: { result: EvaluationResult; source: string }) {
  const rows = result.summary_df.filter((row) => typeof row.model === 'string' && row.model.length > 0);
  const shown = rows.slice(0, 4);
  return <section className="sidebar-run" aria-label="当前评测">
    <span className="sidebar-run-label">当前评测</span>
    <p className={`sidebar-run-gate ${result.gate_passed ? 'passed' : 'blocked'}`}><span className="sidebar-run-dot" aria-hidden="true"/>{`原始门禁 ${result.gate_passed ? 'PASS' : 'FAIL'}`}</p>
    <p className="sidebar-run-source" title={source}>{source}</p>
    <ul aria-label="参评模型">{shown.map((row, index) => <li key={String(row.model)} title={String(row.model)}><span className="sidebar-run-swatch" aria-hidden="true" style={{ background: MODEL_COLORS[index % MODEL_COLORS.length] }}/><span className="sidebar-run-model">{modelName(row.model)}</span></li>)}{rows.length > shown.length && <li className="sidebar-run-more">{`另有 ${rows.length - shown.length} 个模型`}</li>}</ul>
    <p className="sidebar-run-time">{`总耗时 ${seconds(result.total_elapsed_seconds)}`}</p>
  </section>;
}

export default function App({ entryRequest, onHome }: { entryRequest?: EntryRequest; onHome?: () => void }) {
  const [view, setView] = useState<DashboardView>('overview');
  const [navGroup, setNavGroup] = useState(() => groupOf(view));
  const groupTabs = useRef<(HTMLButtonElement | null)[]>([]);
  const [config, setConfig] = useState(initialConfig);
  const [readingTarget, setReadingTarget] = useState<{ view: DashboardView; id: string } | null>(null);
  const [run, dispatch] = useReducer(runReducer, initialRunState);
  const [historyOpen, setHistoryOpen] = useState(false);
  const historyToggle = useRef<HTMLButtonElement>(null);
  const [saving, setSaving] = useState(false);
  const [saveNotice, setSaveNotice] = useState<{ result: EvaluationResult; error: boolean; text: string } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [health, setHealth] = useState<'checking' | 'connected' | 'disconnected'>('checking');
  const [configurationCheck, setConfigurationCheck] = useState<ConfigurationCheck | null>(null);
  const [registeredModels, setRegisteredModels] = useState<ModelEntry[]>(() => BYOK ? byokEntries() : []);
  const [progressMessage, setProgressMessage] = useState('');
  // A fresh token per loaded result lets the overview replay the gate settle on every load, but not when switching views.
  const [resultLoad, setResultLoad] = useState<object>(() => ({}));
  const inflight = useRef(false);
  const configurationInflight = useRef(false);
  const healthRequest = useRef(0);
  const guideWorkspaceVisit = useRef(false);
  const mounted = useRef(true);
  const current = navigation.find((item) => item.id === view)!;
  const running = run.status === 'running';
  const checkingConfiguration = configurationCheck?.status === 'checking';
  const stale = Boolean(run.result && ['running', 'failed', 'unknown'].includes(run.status));
  const isDemo = run.source === 'demo';
  const groupIndex = Math.max(0, NAV_GROUPS.findIndex((group) => group.id === navGroup));

  // Any change of view, Back and deep links included, reopens the group that holds it; a tab alone only browses.
  useEffect(() => { setNavGroup(groupOf(view)); }, [view]);

  /** Tabs pattern: arrow keys, Home and End move between groups and open them, so only the selected tab is a Tab stop. */
  const moveGroupFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    const last = NAV_GROUPS.length - 1;
    const next = event.key === 'ArrowRight' ? (groupIndex === last ? 0 : groupIndex + 1)
      : event.key === 'ArrowLeft' ? (groupIndex === 0 ? last : groupIndex - 1)
        : event.key === 'Home' ? 0 : event.key === 'End' ? last : null;
    if (next === null) return;
    event.preventDefault();
    setNavGroup(NAV_GROUPS[next].id);
    groupTabs.current[next]?.focus();
  };

  /** User navigation gets its own history entry, so views can be shared and Back returns to the previous one. */
  const navigate = (next: DashboardView) => {
    setHistoryOpen(false);
    setReadingTarget(null);
    setView(next);
    if (!onHome || !parseLabHash(window.location.hash)) return;
    const hash = labHash(next, isDemo, next === 'knowledge-update' ? parseLabHash(window.location.hash)?.step : undefined);
    if (window.location.hash === hash) return;
    window.history.replaceState({ ...window.history.state, labView: view }, '');
    window.history.pushState({ labEntryHandled: true, labView: next }, '', hash);
  };

  const navigateGuide = (next: DashboardView) => {
    // Merely visiting a tutorial step must not reconcile or replace selected models.
    guideWorkspaceVisit.current = next === 'workspace' && view !== 'workspace';
    navigate(next);
  };

  // Cards marked data-spotlight light up under the pointer; one listener serves them all.
  useEffect(() => {
    const move = (event: PointerEvent) => {
      const card = (event.target as Element | null)?.closest?.<HTMLElement>('[data-spotlight]');
      if (!card) return;
      const box = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${event.clientX - box.left}px`);
      card.style.setProperty('--my', `${event.clientY - box.top}px`);
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => window.removeEventListener('pointermove', move);
  }, []);

  useEffect(() => {
    if (!onHome) return;
    // An entry without a view segment (e.g. "进入工作台") keeps the current view, matching the mounted-workbench behaviour.
    const restore = () => {
      const route = parseLabHash(window.location.hash);
      if (!route) return;
      const next = (window.history.state?.labView as DashboardView | undefined) ?? route.view;
      if (next) setView(next);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [onHome]);

  const refreshHealth = useCallback(async () => {
    const request = ++healthRequest.current;
    setHealth('checking');
    const connected = await checkHealth();
    if (mounted.current && request === healthRequest.current) setHealth(connected ? 'connected' : 'disconnected');
    return connected;
  }, []);

  /** The backend supplies the current built-ins; renamed or removed models leave the selection. */
  const applyModels = useCallback((models: ModelEntry[]) => {
    setRegisteredModels(models);
    const builtin = models.filter((model) => model.source === 'builtin').map((model) => model.name);
    const known = new Set<string>([...(BYOK ? [] : builtin.length ? builtin : MODELS.map((model) => model.id)), ...models.map((model) => model.name)]);
    setConfig((current) => {
      if (BYOK) {
        const selected = current.modelNames.filter((name) => known.has(name));
        return { ...current, modelNames: selected.length ? selected : models.slice(0, 1).map((model) => model.name), judgeModelName: current.judgeModelName && known.has(current.judgeModelName) ? current.judgeModelName : undefined };
      }
      const defaults = current.modelNames.length === MODELS.length && MODELS.every((model) => current.modelNames.includes(model.id));
      return { ...current, modelNames: defaults && builtin.length ? builtin : current.modelNames.filter((name) => known.has(name)) };
    });
  }, []);

  // Read on opening the workspace, not on start, so the first screen stays a health check only.
  useEffect(() => {
    if (view !== 'workspace' || READ_ONLY || STATIC_SITE) return;
    if (BYOK) { applyModels(byokEntries()); return; }
    if (guideWorkspaceVisit.current) { guideWorkspaceVisit.current = false; return; }
    const controller = new AbortController();
    // Without a backend the workspace simply keeps the built-in models.
    fetchModels(controller.signal).then(applyModels).catch(() => undefined);
    return () => controller.abort();
  }, [view, applyModels]);

  useEffect(() => {
    mounted.current = true;
    if (!STATIC_SITE) void refreshHealth();
    return () => { mounted.current = false; healthRequest.current += 1; };
  }, [refreshHealth]);

  const checkConfiguration = async () => {
    if (inflight.current || configurationInflight.current) return;
    setView('workspace');
    const message = validateConfig(config);
    setFormError(message);
    if (message) {
      setConfigurationCheck({ status: 'error', message: BYOK ? '配置未通过检查。请修正下方提示后重新检查。' : '配置未通过本地检查。请修正下方提示后重新检查。' });
      return;
    }
    configurationInflight.current = true;
    setConfigurationCheck({ status: 'checking', message: BYOK ? '正在校验参数并检查 API 连接…' : '正在校验本地参数并检查 API 连接…' });
    try {
      const connected = await refreshHealth();
      if (mounted.current) setConfigurationCheck({
        status: connected ? 'success' : 'error',
        message: BYOK ? connected ? '参数检查通过，云端服务可连接。' : '参数检查通过，但云端服务未连接。请稍后重新检查。' : connected ? '本地参数检查通过，服务可连接。' : '本地参数检查通过，但服务未连接。请确认 FastAPI 服务正在运行，再重新检查。',
      });
    } finally {
      configurationInflight.current = false;
    }
  };

  const loadDemo = useCallback(() => {
    if (inflight.current || configurationInflight.current) return;
    if (!isEvaluationResult(demo.result)) { setFormError('演示快照结构不完整。'); return; }
    setHistoryOpen(false);
    dispatch({ type: 'demo', result: demo.result });
    setResultLoad({});
    setFormError(null);
    setConfigurationCheck(null);
    setView('overview');
  }, []);

  const inspectExample = (example: ExampleEvidence) => {
    if (inflight.current || configurationInflight.current) return;
    const next = example === 'trace' ? 'trace' : 'fix';
    const target = example === 'heldout' ? 'fx-heldout-title' : example === 'gate' ? 'fx-gate-title' : 'trace-answer-title';
    if (example !== 'trace') {
      navigate(next);
    } else {
      // This explicit action opens the product snapshot; choosing a document never loads results.
      loadDemo();
      setView(next);
      if (onHome && parseLabHash(window.location.hash)) {
        window.history.replaceState({ ...window.history.state, labView: view }, '');
        window.history.pushState({ labEntryHandled: true, labView: next }, '', labHash(next, true));
      }
    }
    setReadingTarget({ view: next, id: target });
  };

  useEffect(() => {
    if (!readingTarget || readingTarget.view !== view) return;
    const target = document.getElementById(readingTarget.id);
    target?.scrollIntoView?.({ block: 'start' });
    target?.focus({ preventScroll: true });
  }, [readingTarget, view]);

  const consumedEntry = useRef<number | null>(null);
  useEffect(() => {
    if (!entryRequest || consumedEntry.current === entryRequest.id) return;
    consumedEntry.current = entryRequest.id;
    if (entryRequest.target === 'demo') loadDemo();
    if (entryRequest.target === 'workspace') setView('workspace');
    if (entryRequest.view) setView(entryRequest.view);
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }, [entryRequest, loadDemo]);

  const startRun = async () => {
    if (READ_ONLY || inflight.current || configurationInflight.current) return;
    const message = validateConfig(config);
    setFormError(message);
    setConfigurationCheck(null);
    if (message) return;
    setHistoryOpen(false);
    inflight.current = true;
    setProgressMessage(BYOK ? '正在提交评测任务…' : '');
    dispatch({ type: 'start', config });
    try {
      const result = await evaluate(toRequest(config), BYOK ? (message) => { if (mounted.current) setProgressMessage(message); } : undefined);
      if (mounted.current) { dispatch({ type: 'complete', result }); setResultLoad({}); setView('overview'); }
    } catch (error) {
      if (mounted.current) dispatch({ type: 'fail', message: error instanceof Error ? error.message : '评测未完成，请检查服务日志。', uncertain: error instanceof ConnectionError });
    } finally {
      inflight.current = false;
    }
  };

  const openRecord = (record: EvaluationRecord) => {
    if (inflight.current || configurationInflight.current) return;
    dispatch({ type: 'history', record });
    if (record.request) setConfig(fromRequest(record.request));
    setHistoryOpen(false);
    setResultLoad({});
    setFormError(null);
    setConfigurationCheck(null);
    setView('overview');
    document.getElementById('main-content')?.focus({ preventScroll: true });
  };

  const restoreResult = () => {
    if (!run.lastReal || running || checkingConfiguration) return;
    setHistoryOpen(false);
    if (run.lastReal.submittedConfig) setConfig(run.lastReal.submittedConfig);
    dispatch({ type: 'restore' });
    setResultLoad({});
    setView('overview');
  };

  const saveRecord = async () => {
    const result = run.result;
    if (!result || isDemo || saving || running) return;
    setSaving(true);
    setSaveNotice(null);
    try {
      if (result.record_id) {
        downloadRecordJson(await downloadEvaluationRecord(result.record_id));
        if (mounted.current) setSaveNotice({ result, error: false, text: '已下载服务中保存的完整 JSON 记录，可从历史记录入口导入。' });
      } else {
        const record: EvaluationRecord = {
          schema_version: 1, id: crypto.randomUUID().replaceAll('-', ''), created_at: new Date().toISOString(), source: 'evaluation',
          request: run.lastReal?.result === result ? run.lastReal.request : null, result,
          report_html: null, chart_png_base64: null, artifact_warnings: ['浏览器结果备份未包含报告与图表文件。'],
        };
        downloadRecordJson(record);
        if (mounted.current) setSaveNotice({ result, error: false, text: '已下载 JSON 备份；未保存到服务历史。可从历史记录入口导入，报告与图表文件未包含在备份中。' });
      }
    } catch (reason) {
      if (mounted.current) setSaveNotice({ result, error: true, text: reason instanceof Error ? reason.message : '记录下载失败，当前结果仍保留。' });
    } finally {
      if (mounted.current) setSaving(false);
    }
  };

  const resultMeta = run.result && <div className="result-toolbar"><div><span className={`badge ${run.source === 'demo' ? 'demo' : 'neutral'}`}>{run.source === 'demo' ? '演示快照' : run.source === 'history' ? '历史评测记录' : '真实评测结果'}</span>{stale && <span className="badge warning">上次结果 · 本次尚未更新</span>}<span className="muted">{run.source === 'demo' ? demo.provenance.label : run.source === 'history' ? `保存时间 ${run.result.saved_at || '未记录'} · 未重新评测` : '本次接口返回结果'}</span></div><span className="muted">总评测耗时 <strong>{seconds(run.result.total_elapsed_seconds)}</strong></span></div>;
  const savedRequest = run.lastReal?.request;
  const gateRequest = run.lastReal?.result === run.result && savedRequest ? {
    useDefaultThresholds: savedRequest.faith_threshold === null && savedRequest.ctx_prec_threshold === null,
    faithThreshold: savedRequest.faith_threshold, contextThreshold: savedRequest.ctx_prec_threshold,
  } : null;

  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href={labHash('overview', isDemo)} onClick={(event) => { event.preventDefault(); navigate('overview'); }}><span className="brand-mark"><BrandMark size={26}/></span><span><strong>鉴真全域</strong><small>VeriScope AI</small></span></a>
      <nav aria-label="工作区导航">{NAV_GROUPS.map((group) => <div key={group.id} id={`${group.id}-panel`} className={`nav-group nav-group--${group.tone}${group.id === navGroup ? ' is-open' : ''}`} role="group" aria-labelledby={group.id}>
        <span className="nav-group-label" id={group.id}>{group.label}</span>
        {group.views.map((id) => navigation.find((item) => item.id === id)!).map((item, index) => <button key={item.id} className={`nav-item ${view === item.id ? 'active' : ''}`} style={{ '--nav-order': index } as CSSProperties} title={item.description} onClick={() => navigate(item.id)} aria-current={view === item.id ? 'page' : undefined}><span className="nav-icon" aria-hidden="true"><Icon name={item.icon} size={17}/></span><span>{item.label}</span></button>)}
      </div>)}</nav>
      {run.result ? <SidebarRun result={run.result} source={isDemo ? `演示快照 ${demoVersion}` : run.source === 'history' ? '来源 · 历史评测记录' : '来源 · 本次接口返回'}/> : <p className="sidebar-run-empty">尚未载入评测结果</p>}
      <div className="sidebar-foot"><span className="sidebar-rule"/><a className="sidebar-repo" href={REPO_URL} target="_blank" rel="noopener noreferrer"><Icon name="link" size={14}/>Gitee 源码 · Apache-2.0</a></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb">{onHome ? <button className="btn btn-ghost" onClick={onHome}>返回首页</button> : <span>VeriScope AI</span>}<Icon name="chevron" size={13}/><strong>{current.label}</strong></div>
        <div className="nav-tabs" role="tablist" aria-label="导航分组" data-tone={NAV_GROUPS[groupIndex].tone} style={{ '--nav-tab-index': groupIndex } as CSSProperties} onKeyDown={moveGroupFocus}>
          <span className="nav-tabs-indicator" aria-hidden="true"/>
          {NAV_GROUPS.map((group, index) => <button key={group.id} ref={(element) => { groupTabs.current[index] = element; }} type="button" role="tab" id={`${group.id}-tab`} className={`nav-tab nav-tab--${group.tone}`} aria-selected={index === groupIndex} aria-controls={`${group.id}-panel`} tabIndex={index === groupIndex ? 0 : -1} onClick={() => setNavGroup(group.id)}>{group.label}{index !== groupIndex && group.views.includes(view) && <span className="nav-tab-mark" aria-hidden="true"/>}</button>)}
        </div>
        <EvaluationGuide view={view} onNavigate={navigateGuide} hasResult={Boolean(run.result)} isDemo={isDemo} busy={running || checkingConfiguration} readOnly={READ_ONLY} staticSite={STATIC_SITE} allowWelcome={!entryRequest?.view && entryRequest?.target !== 'demo' && !parseLabHash(window.location.hash)?.view}/>
        {STATIC_SITE ? <div className="health-status static" title="静态备用站只展示仓库内的冻结快照，没有后端服务"><span className="status-dot"/>静态演示站 · 无后端</div> : <div className={`health-status ${health}`}><span className="status-dot"/>{health === 'checking' ? '检查服务连接' : health === 'connected' ? '服务可连接' : '服务未连接'}<button className="icon-button" aria-label="重新检查服务连接" title="仅检查 API 是否可连接" disabled={health === 'checking'} onClick={() => { void refreshHealth(); }}><Icon name="refresh" size={14}/></button></div>}</header>
      <main id="main-content" tabIndex={-1}><div className={`page-heading${view === 'verification' ? ' verification-page-heading' : ''}${view === 'citation-version' ? ' cv-page-heading' : ''}`}><div><h1>{current.title}</h1><p>{view === 'workspace' && READ_ONLY ? '先看资料用途，再跟随已有案例了解评测方法。' : current.description}</p></div>{view !== 'citation-version' && view !== 'knowledge-update' && <div className="page-actions evaluation-record-actions" data-evaluation-guide="history">{isDemo && run.lastReal && <button type="button" className="btn btn-secondary" onClick={restoreResult} disabled={running || checkingConfiguration}>返回评测结果</button>}<button type="button" className="btn btn-secondary" data-evaluation-guide="save" onClick={() => { void saveRecord(); }} disabled={!run.result || isDemo || running || saving || READ_ONLY || STATIC_SITE}><Icon name="file" size={15}/>{saving ? '正在下载…' : '保存记录'}</button><button ref={historyToggle} type="button" className="btn btn-secondary" aria-expanded={historyOpen} aria-controls="evaluation-history" disabled={READ_ONLY || STATIC_SITE} onClick={() => setHistoryOpen((open) => !open)}><Icon name="folder" size={15}/>历史记录</button><button className="btn btn-secondary" onClick={loadDemo} disabled={running || checkingConfiguration}><Icon name="play" size={15}/>加载演示</button><button className={`btn ${view === 'workspace' || view === 'report' || (view === 'overview' && !run.result) ? 'btn-secondary' : 'btn-primary'}`} data-evaluation-guide="new" onClick={() => navigate('workspace')}><Icon name="layers" size={16}/>新建评测</button></div>}</div>
        {historyOpen && <EvaluationHistory disabled={running || checkingConfiguration} onOpen={openRecord} onClose={() => { setHistoryOpen(false); historyToggle.current?.focus(); }}/>}
        {run.result?.save_error && !isDemo && <p className="record-notice record-notice--error">{run.result.save_error}</p>}
        {saveNotice?.result === run.result && <p className={`record-notice${saveNotice.error ? ' record-notice--error' : ''}`} role={saveNotice.error ? 'alert' : 'status'}>{saveNotice.text}</p>}
        {running && view !== 'workspace' && <div className="run-notice" role="status"><span className="busy-dot"/><strong>评测中</strong><span>{BYOK ? progressMessage : '正在等待评测完成，请保持页面打开。'}</span><div className="operation-progress" role="progressbar" aria-label="评测进度"><span/></div></div>}
        {run.error && <div className={`error-notice ${run.status === 'unknown' ? 'uncertain' : ''}`} role="alert"><Icon name="alert"/><div><strong>{run.status === 'unknown' ? '评测结果未知' : '本次评测未完成'}</strong><p>{run.error}</p></div><button className="btn btn-ghost" disabled={checkingConfiguration} onClick={() => { void checkConfiguration(); }}>{checkingConfiguration ? '检查中…' : '检查配置'}<Icon name={checkingConfiguration ? 'refresh' : 'arrow'} size={14}/></button></div>}
        {view !== 'overview' && view !== 'models' && view !== 'audit' && view !== 'fix' && view !== 'robustness' && view !== 'blindspot' && view !== 'annotate' && view !== 'parent-context' && view !== 'citation-version' && view !== 'knowledge-update' && resultMeta}
        {view === 'overview' && <div data-evaluation-guide="result"><OverviewView result={run.result} resultLoad={resultLoad} resultMeta={resultMeta} gateProvenance={run.source === 'demo' ? demo.provenance : undefined} gateRequest={gateRequest} onConfigure={() => navigate('workspace')} onDemo={loadDemo} onCompare={() => navigate('comparison')} onTrace={() => navigate('trace')} demoDisabled={running || checkingConfiguration}/></div>}
        {view === 'workspace' && <WorkspaceView config={config} onChange={(next) => { setConfig(next); setFormError(null); setConfigurationCheck(null); }} onRun={() => { void startRun(); }} running={running} progressMessage={progressMessage} readOnly={READ_ONLY} hasLiveResult={run.source === 'live'} error={formError} configurationCheck={configurationCheck} registeredModels={registeredModels} onInspectExample={inspectExample}/>}
        {view === 'models' && <ModelsView onModelsChange={applyModels}/>}
        {view === 'comparison' && <ComparisonView result={run.result}/>}
        {view === 'trace' && <div data-evaluation-guide="trace"><TraceView result={run.result} document={run.source === 'demo' ? demoDocument : null}/></div>}
        {view === 'report' && <div data-evaluation-guide="report"><ReportView result={run.result}/></div>}
        {view === 'verification' && <EvaluationVerificationView result={run.result} source={run.source}/>}
        {view === 'audit' && <GateAuditView/>}
        {view === 'fix' && <FixVerificationView/>}
        {view === 'robustness' && <RobustnessView/>}
        {view === 'blindspot' && <BlindspotView/>}
        {view === 'annotate' && <AnnotateView/>}
        {view === 'parent-context' && <ParentContextView/>}
        {view === 'citation-version' && <CitationVersionView/>}
        {view === 'knowledge-update' && <KnowledgeUpdateView/>}
        {run.source === 'demo' && view !== 'workspace' && view !== 'models' && view !== 'audit' && view !== 'fix' && view !== 'robustness' && view !== 'blindspot' && view !== 'annotate' && view !== 'parent-context' && view !== 'citation-version' && view !== 'knowledge-update' && <details className="demo-provenance"><summary>查看演示数据来源</summary><p>{demo.provenance.description}</p><p>快照仅用于离线展示，加载时未执行模型评测。</p><ul>{demo.provenance.files.map((file) => <li key={file}>{file}</li>)}</ul></details>}
        <footer className="page-footer"><span>鉴真全域 VeriScope AI</span><span>逐句核对 / 证据溯源 / 质量门禁</span></footer>
      </main>
    </div>
  </div>;
}
