import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { checkHealth, ConnectionError, evaluate, isEvaluationResult } from './api/client';
import type { DemoFixture, EvaluationResult } from './api/types';
import { Icon } from './components/Icon';
import type { IconName } from './components/Icon';
import { OverviewView } from './features/OverviewView';
import { WorkspaceView } from './features/WorkspaceView';
import type { ConfigurationCheck } from './features/WorkspaceView';
import { ComparisonView } from './features/ComparisonView';
import { TraceView } from './features/TraceView';
import { ReportView } from './features/ReportView';
import { EvaluationVerificationView } from './features/EvaluationVerificationView';
import demoData from './fixtures/demo.json';
import { MODEL_COLORS, READ_ONLY, REPO_URL } from './config';
import { labHash, parseLabHash } from './labRoute';
import type { LabView } from './labRoute';
import { initialConfig, initialRunState, runReducer, toRequest, validateConfig } from './state';
import { modelName, seconds } from './utils/format';
import type { EntryRequest } from './AppEntry';

const demo = demoData as DemoFixture;
const demoRun = demo.provenance.run as { version?: unknown } | undefined;
const demoVersion = typeof demoRun?.version === 'string' ? demoRun.version : demo.provenance.label;
type DashboardView = LabView;
const navigation: { id: DashboardView; label: string; icon: IconName; title: string; description: string }[] = [
  { id: 'overview', label: '评测总览', icon: 'overview', title: '评测总览', description: '从质量门禁到逐句证据，检查模型回答的可靠性。' },
  { id: 'workspace', label: '评测工作区', icon: 'layers', title: '评测工作区', description: '选择数据和模型，运行一次可解释的可靠性评测。' },
  { id: 'comparison', label: '多模型对比', icon: 'chart', title: '多模型对比', description: '同一测试集上的指标与耗时，保留原有模型顺序。' },
  { id: 'trace', label: '幻觉分析', icon: 'trace', title: '幻觉分析', description: '阅读模型回答，逐句检查事实判断与检索证据。' },
  { id: 'report', label: '评测报告', icon: 'file', title: '评测报告', description: '查看已生成的 HTML 报告，保留原始评测内容。' },
  { id: 'verification', label: '评测验证', icon: 'check', title: '评测验证', description: '查看人工核验状态与原始依据。' },
];

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
  const [config, setConfig] = useState(initialConfig);
  const [run, dispatch] = useReducer(runReducer, initialRunState);
  const [formError, setFormError] = useState<string | null>(null);
  const [health, setHealth] = useState<'checking' | 'connected' | 'disconnected'>('checking');
  const [configurationCheck, setConfigurationCheck] = useState<ConfigurationCheck | null>(null);
  const inflight = useRef(false);
  const configurationInflight = useRef(false);
  const healthRequest = useRef(0);
  const mounted = useRef(true);
  const current = navigation.find((item) => item.id === view)!;
  const running = run.status === 'running';
  const checkingConfiguration = configurationCheck?.status === 'checking';
  const stale = Boolean(run.result && ['running', 'failed', 'unknown'].includes(run.status));
  const isDemo = run.source === 'demo';

  /** User navigation gets its own history entry, so views can be shared and Back returns to the previous one. */
  const navigate = (next: DashboardView) => {
    setView(next);
    if (!onHome || !parseLabHash(window.location.hash)) return;
    const hash = labHash(next, isDemo);
    if (window.location.hash === hash) return;
    window.history.replaceState({ ...window.history.state, labView: view }, '');
    window.history.pushState({ labEntryHandled: true, labView: next }, '', hash);
  };

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

  useEffect(() => {
    mounted.current = true;
    void refreshHealth();
    return () => { mounted.current = false; healthRequest.current += 1; };
  }, [refreshHealth]);

  const checkConfiguration = async () => {
    if (inflight.current || configurationInflight.current) return;
    setView('workspace');
    const message = validateConfig(config);
    setFormError(message);
    if (message) {
      setConfigurationCheck({ status: 'error', message: '配置未通过本地检查。请修正下方提示后重新检查。' });
      return;
    }
    configurationInflight.current = true;
    setConfigurationCheck({ status: 'checking', message: '正在校验本地参数并检查 API 连接…' });
    try {
      const connected = await refreshHealth();
      if (mounted.current) setConfigurationCheck({
        status: connected ? 'success' : 'error',
        message: connected ? '本地参数检查通过，服务可连接。' : '本地参数检查通过，但服务未连接。请确认 FastAPI 服务正在运行，再重新检查。',
      });
    } finally {
      configurationInflight.current = false;
    }
  };

  const loadDemo = useCallback(() => {
    if (inflight.current || configurationInflight.current) return;
    if (!isEvaluationResult(demo.result)) { setFormError('演示快照结构不完整。'); return; }
    dispatch({ type: 'demo', result: demo.result });
    setFormError(null);
    setConfigurationCheck(null);
    setView('overview');
  }, []);

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
    inflight.current = true;
    dispatch({ type: 'start', config });
    try {
      const result = await evaluate(toRequest(config));
      if (mounted.current) { dispatch({ type: 'complete', result }); setView('overview'); }
    } catch (error) {
      if (mounted.current) dispatch({ type: 'fail', message: error instanceof Error ? error.message : '评测未完成，请检查服务日志。', uncertain: error instanceof ConnectionError });
    } finally {
      inflight.current = false;
    }
  };

  const resultMeta = run.result && <div className="result-toolbar"><div><span className={`badge ${run.source === 'demo' ? 'demo' : 'neutral'}`}>{run.source === 'demo' ? '演示快照' : '真实评测结果'}</span>{stale && <span className="badge warning">上次结果 · 本次尚未更新</span>}<span className="muted">{run.source === 'demo' ? demo.provenance.label : '本次接口返回结果'}</span></div><span className="muted">总评测耗时 <strong>{seconds(run.result.total_elapsed_seconds)}</strong></span></div>;

  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href={labHash('overview', isDemo)} onClick={(event) => { event.preventDefault(); navigate('overview'); }}><span className="brand-mark"><Icon name="shield" size={27}/></span><span><strong>鉴真全域</strong><small>VeriScope AI</small></span></a>
      <span className="navigation-label">实验工作台</span><nav aria-label="工作区导航">{navigation.map((item) => <button key={item.id} className={`nav-item ${view === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)} aria-current={view === item.id ? 'page' : undefined}><Icon name={item.icon}/><span>{item.label}</span>{view === item.id && <span className="nav-active-mark"/>}</button>)}</nav>
      {run.result ? <SidebarRun result={run.result} source={isDemo ? `演示快照 ${demoVersion}` : '来源 · 本次接口返回'}/> : <p className="sidebar-run-empty">尚未载入评测结果</p>}
      <div className="sidebar-foot"><span className="sidebar-rule"/><a className="sidebar-repo" href={REPO_URL} target="_blank" rel="noopener noreferrer"><Icon name="link" size={14}/>GitHub 源码 · MIT</a></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb">{onHome ? <button className="btn btn-ghost" onClick={onHome}>返回首页</button> : <span>VeriScope AI</span>}<Icon name="chevron" size={13}/><strong>{current.label}</strong></div><div className={`health-status ${health}`}><span className="status-dot"/>{health === 'checking' ? '检查服务连接' : health === 'connected' ? '服务可连接' : '服务未连接'}<button className="icon-button" aria-label="重新检查服务连接" title="仅检查 API 是否可连接" disabled={health === 'checking'} onClick={() => { void refreshHealth(); }}><Icon name="refresh" size={14}/></button></div></header>
      <main id="main-content" tabIndex={-1}><div className={`page-heading${view === 'verification' ? ' verification-page-heading' : ''}`}><div><h1>{current.title}</h1><p>{current.description}</p></div><div className="page-actions"><button className="btn btn-secondary" onClick={loadDemo} disabled={running || checkingConfiguration}><Icon name="play" size={15}/>加载演示</button><button className={`btn ${view === 'workspace' || view === 'report' || (view === 'overview' && !run.result) ? 'btn-secondary' : 'btn-primary'}`} onClick={() => navigate('workspace')}><Icon name="layers" size={16}/>新建评测</button></div></div>
        {running && <div className="run-notice" role="status"><span className="busy-dot"/><strong>评测中</strong><span>正在等待评测完成，请保持页面打开。</span></div>}
        {run.error && <div className={`error-notice ${run.status === 'unknown' ? 'uncertain' : ''}`} role="alert"><Icon name="alert"/><div><strong>{run.status === 'unknown' ? '评测结果未知' : '本次评测未完成'}</strong><p>{run.error}</p></div><button className="btn btn-ghost" disabled={checkingConfiguration} onClick={() => { void checkConfiguration(); }}>{checkingConfiguration ? '检查中…' : '检查配置'}<Icon name={checkingConfiguration ? 'refresh' : 'arrow'} size={14}/></button></div>}
        {view !== 'overview' && resultMeta}
        {view === 'overview' && <OverviewView result={run.result} resultMeta={resultMeta} gateProvenance={run.source === 'demo' ? demo.provenance : undefined} gateRequest={run.source === 'live' && run.status === 'completed' ? run.submittedConfig : null} onConfigure={() => navigate('workspace')} onDemo={loadDemo} onCompare={() => navigate('comparison')} onTrace={() => navigate('trace')} demoDisabled={running || checkingConfiguration}/>}
        {view === 'workspace' && <WorkspaceView config={config} onChange={(next) => { setConfig(next); setFormError(null); setConfigurationCheck(null); }} onRun={() => { void startRun(); }} running={running} readOnly={READ_ONLY} hasLiveResult={run.source === 'live'} error={formError} configurationCheck={configurationCheck}/>}
        {view === 'comparison' && <ComparisonView result={run.result}/>}
        {view === 'trace' && <TraceView result={run.result}/>}
        {view === 'report' && <ReportView result={run.result}/>}
        {view === 'verification' && <EvaluationVerificationView result={run.result} source={run.source}/>}
        {run.source === 'demo' && view !== 'workspace' && <details className="demo-provenance"><summary>查看演示数据来源</summary><p>{demo.provenance.description}</p><p>快照仅用于离线展示，加载时未执行模型评测。</p><ul>{demo.provenance.files.map((file) => <li key={file}>{file}</li>)}</ul></details>}
        <footer className="page-footer"><span>鉴真全域 VeriScope AI</span><span>逐句核对 / 证据溯源 / 质量门禁</span></footer>
      </main>
    </div>
  </div>;
}
