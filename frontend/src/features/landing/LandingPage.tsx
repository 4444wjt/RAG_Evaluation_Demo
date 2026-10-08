import { useEffect, useState } from 'react';
import { Icon } from '../../components/Icon';
import { EvidenceFlow } from './EvidenceFlow';
import { EvidenceJourney } from './EvidenceJourney';
import { ResearchField } from './ResearchField';
import { EvidenceTracePreview, QualityGatePreview } from './ProductPreview';
import './landing.css';

export type LabTarget = 'lab' | 'demo' | 'workspace';

const capabilities = [
  { icon: 'layers', title: '评测工作区', description: '选择数据与模型，配置评测参数。' },
  { icon: 'chart', title: '多模型对比', description: '在同一测试集上比较指标与耗时。' },
  { icon: 'trace', title: '幻觉分析', description: '从用例进入逐句判断与上下文证据。' },
  { icon: 'file', title: '评测报告', description: '导入并查看已有 HTML 评测报告。' },
] as const;

export function LandingPage({ onEnter }: { onEnter: (target: LabTarget) => void }) {
  const [reduced, setReduced] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const motionEnabled = !reduced;
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(preference.matches);
    preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);
  const enter = (target: LabTarget) => (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); onEnter(target);
  };
  return <div className="landing-page" data-motion={motionEnabled ? 'on' : 'off'}>
    <a className="landing-skip" href="#landing-content">跳到主要内容</a>
    <header className="landing-header"><a href="#/" className="landing-brand" aria-label="鉴真全域 VeriScope AI 首页"><Icon name="shield" size={28}/><span><strong>鉴真全域</strong><small>VeriScope AI</small></span></a><nav aria-label="首页导航"><a href="#verification">验证流程</a><a href="#platform">平台能力</a><a className="btn btn-secondary" href="#/lab" onClick={enter('lab')}>进入工作台<Icon name="arrow" size={16}/></a></nav></header>
    <main id="landing-content" className="landing-main" tabIndex={-1}>
      <ResearchField enabled={motionEnabled}/>
      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero-copy"><h1 id="landing-title">让 <b className="landing-latin">RAG</b> 的回答，<br/>经得起<span>证据检查。</span></h1><p className="landing-intro">从多模型评测到逐句证据溯源，<br className="landing-desktop-break"/>让每一次可靠性判断，都有可检查的依据。</p><div className="landing-actions"><a className="btn btn-primary" href="#/lab/demo" onClick={enter('demo')}><Icon name="play" size={17}/>查看演示</a><a className="landing-text-link" href="#/lab/workspace" onClick={enter('workspace')}>配置评测<Icon name="arrow" size={17}/></a></div><p className="landing-demo-note">已有评测快照 · 无需连接模型服务</p><div className="landing-models"><span>多模型，统一评测视角</span><ul aria-label="演示包含的模型"><li>DeepSeek</li><li>Qwen2.5-7B</li><li>GLM-5.2-Fast</li></ul></div></div>
        <EvidenceFlow enabled={motionEnabled}/>
      </section>
      <section className="landing-proof" id="verification" aria-labelledby="verification-title"><div className="landing-section-heading"><h2 id="verification-title">看见结论，也看见依据。</h2><p>从实验结果继续追问，直到回答中的每一句陈述。</p></div>
        <article className="landing-proof-row">
          <div className="landing-proof-copy"><Icon name="shield" size={25}/><h3>先读实验结论</h3><p>模型、样本范围、指标与阈值分组呈现，判定依据可回查。</p></div>
          <figure><a className="landing-proof-link" href="#/lab/demo" onClick={enter('demo')} aria-label="进入演示查看质量门禁"><div className="landing-preview-heading"><Icon name="shield" size={16}/><span>质量门禁</span><span className="landing-preview-source">DeepSeek</span></div><QualityGatePreview/><span className="landing-preview-action">进入实验工作台<Icon name="arrow" size={14}/></span></a><figcaption>真实界面 · 演示快照 20260930-1958 · 非本次实时评测</figcaption></figure>
        </article>
        <EvidenceJourney enabled={motionEnabled}/>
        <article className="landing-proof-row landing-proof-reverse landing-proof-evidence">
          <div className="landing-proof-copy"><Icon name="trace" size={25}/><h3>再沿证据回查</h3><p>对照事实判断与检索原文，逐句回查陈述依据。</p></div>
          <figure><a className="landing-proof-link" href="#/lab/demo" onClick={enter('demo')} aria-label="进入演示检查原始证据"><div className="landing-preview-heading"><Icon name="trace" size={16}/><span>逐句溯源</span><span className="landing-preview-source">原始记录</span></div><EvidenceTracePreview/><span className="landing-preview-action">进入实验工作台<Icon name="arrow" size={14}/></span></a><figcaption>真实界面 · 同一演示快照中的逐句证据</figcaption></figure>
        </article>
      </section>
      <section className="landing-capabilities" id="platform" aria-labelledby="platform-title"><div className="landing-section-heading"><h2 id="platform-title">一条完整的评测路径。</h2></div><dl>{capabilities.map((item, index) => <div key={item.title}><dt><span className="landing-capability-node" aria-hidden="true"><Icon name={item.icon} size={22}/><span>{String(index + 1).padStart(2, '0')}</span></span><span>{item.title}</span></dt><dd>{item.description}</dd></div>)}</dl></section>
      <section className="landing-closing"><div><h2>从一份真实快照开始。</h2><p>进入实验工作台，亲自检查模型的回答与依据。</p></div><a className="btn btn-primary" href="#/lab/demo" onClick={enter('demo')}>查看演示<Icon name="arrow" size={17}/></a></section>
    </main>
    <footer className="landing-footer"><span>鉴真全域 VeriScope AI</span><span>评测结果 / 事实判断 / 上下文证据</span><a href="#/lab/workspace" onClick={enter('workspace')}>配置评测<Icon name="arrow" size={14}/></a></footer>
  </div>;
}
