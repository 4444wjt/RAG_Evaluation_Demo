import { Component, lazy, Suspense, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { LandingPage } from './features/landing/LandingPage';
import { labHash, parseLabHash } from './labRoute';
import type { LabTarget, LabView } from './labRoute';

const Dashboard = lazy(() => import('./App'));
const isLab = () => parseLabHash(window.location.hash) !== null;
const initialEntry = (): { target: LabTarget; view?: LabView } => parseLabHash(window.location.hash) ?? { target: 'lab' };
const markEntryHandled = () => window.history.replaceState({ ...window.history.state, labEntryHandled: true }, '');
export type EntryRequest = { id: number; target: LabTarget; view?: LabView };

class DashboardBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <section className="landing-load-state" role="alert"><h1>工作台暂未加载成功</h1><p>请重新加载页面，或返回首页。</p><button className="btn btn-primary" onClick={() => window.location.reload()}>重新加载</button> <a className="btn btn-secondary" href="#/">返回首页</a></section> : this.props.children;
  }
}

export default function AppEntry() {
  const [showLab, setShowLab] = useState(isLab);
  const [visited, setVisited] = useState(isLab);
  const [entry, setEntry] = useState<EntryRequest>(() => ({ id: 0, ...initialEntry() }));

  useEffect(() => {
    if (isLab()) markEntryHandled();
    const update = () => {
      const next = isLab();
      setShowLab(next);
      if (next) {
        // History navigation resumes the mounted workbench without replaying an entry action.
        if (!window.history.state?.labEntryHandled) {
          setEntry((previous) => ({ id: previous.id + 1, ...initialEntry() }));
          markEntryHandled();
        }
        setVisited(true);
      }
      requestAnimationFrame(() => {
        if (next || window.location.hash === '#/' || !window.location.hash) {
          window.scrollTo(0, 0);
          document.querySelector<HTMLElement>(next ? '#main-content' : '#landing-content')?.focus({ preventScroll: true });
        }
      });
    };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);

  const enter = (target: LabTarget, view?: LabView) => {
    setEntry((previous) => ({ id: previous.id + 1, target, view }));
    setVisited(true);
    setShowLab(true);
    // A view opens that page directly (the case tour is #/lab/fix); it never loads the demo snapshot on its own.
    window.location.hash = view ? labHash(view, target === 'demo').slice(1) : target === 'lab' ? '/lab' : `/lab/${target}`;
    markEntryHandled();
  };

  return <>
    {!showLab && <LandingPage onEnter={enter}/>}
    {visited && <div hidden={!showLab}><DashboardBoundary><Suspense fallback={<section className="landing-load-state" role="status"><h1>正在打开实验工作台…</h1><p>正在加载分析界面。</p><a className="btn btn-secondary" href="#/">返回首页</a></section>}><Dashboard entryRequest={entry} onHome={() => { window.location.hash = '/'; }}/></Suspense></DashboardBoundary></div>}
  </>;
}
