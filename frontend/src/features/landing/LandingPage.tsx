import { useEffect, useState } from 'react';
import { BrandMark } from '../../components/BrandMark';
import { Icon } from '../../components/Icon';
import { SlideArrow } from '../../components/SlideArrow';
import { DEEPSEEK_VERSION, REPO_URL } from '../../config';
import { EvidenceFlow } from './EvidenceFlow';
import { EvidenceJourney } from './EvidenceJourney';
import { ResearchField } from './ResearchField';
import { EvidenceTracePreview, QualityGatePreview } from './ProductPreview';
import './landing.css';

export type { LabTarget } from '../../labRoute';
import type { LabTarget, LabView } from '../../labRoute';

const capabilities = [
  { icon: 'layers', title: '选题与模型', description: '选择文档、测试集与参与评测的模型。' },
  { icon: 'chart', title: '多模型对比', description: '在同一测试集上比较指标与耗时。' },
  { icon: 'trace', title: '逐句核对', description: '从每个回答进入逐句判定与上下文证据。' },
  { icon: 'file', title: '查看报告', description: '导入并查看已有 HTML 评测报告。' },
] as const;

/** 标题里「逐句查到出处」的核对动效播完后，右侧流程图再开始走。 */
const heroMomentMs = 1500;

export function LandingPage({ onEnter }: { onEnter: (target: LabTarget, view?: LabView) => void }) {
  const [reduced, setReduced] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const motionEnabled = !reduced;
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(preference.matches);
    preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);
  const enter = (target: LabTarget, view?: LabView) => (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (view) onEnter(target, view); else onEnter(target);
  };
  return <div className="landing-page" data-motion={motionEnabled ? 'on' : 'off'}>
    <a className="landing-skip" href="#landing-content">跳到主要内容</a>
    <header className="landing-header"><a href="#/" className="landing-brand" aria-label="鉴真全域 VeriScope AI 首页"><BrandMark size={32}/><span><strong>鉴真全域</strong><small>VeriScope AI</small></span></a><nav aria-label="首页导航"><a href="#verification">验证流程</a><a href="#platform">平台能力</a><a href={REPO_URL} target="_blank" rel="noopener noreferrer">Gitee</a><a className="btn btn-secondary" href="#/lab" onClick={enter('lab')}>进入工作台<Icon name="arrow" size={16}/></a></nav></header>
    <main id="landing-content" className="landing-main" tabIndex={-1}>
      <ResearchField enabled={motionEnabled}/>
      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero-copy"><h1 id="landing-title"><b className="landing-latin">AI</b> 的每个回答，<br/>都能<span>逐句查到出处。</span></h1><p className="landing-intro">看到发布拦截的依据，回查回答与原文，核验处理后的结果。</p><div className="landing-actions"><a className="btn btn-primary" href="#/lab/fix" onClick={enter('lab', 'fix')}><Icon name="shield" size={17}/>看一次拦截如何复核</a><a className="landing-text-link" href="#/lab/demo" onClick={enter('demo')}>查看演示<Icon name="arrow" size={17}/></a></div><p className="landing-demo-note">真实归档案例 · 无需连接模型服务，不执行新的评测</p><div className="landing-models"><span>多模型，同一套题横向对比</span><ul aria-label="演示包含的模型"><li>{DEEPSEEK_VERSION}</li><li>Qwen2.5-7B</li><li>GLM-5.2-Fast</li></ul></div></div>
        <EvidenceFlow enabled={motionEnabled} startDelay={heroMomentMs}/>
      </section>
      <section className="landing-proof" id="verification" aria-labelledby="verification-title"><div className="landing-section-heading"><h2 id="verification-title">从一个分数，追到原文那一句。</h2><p>点开任意一条回答，从模型原话看到它引用的原文。</p></div>
        <article className="landing-proof-row">
          <div className="landing-proof-copy"><Icon name="shield" size={25}/><h3>先看总分和阈值</h3><p>每个模型、每道题、每项指标分别列出，够没够线一眼看到。</p></div>
          <figure><a className="landing-proof-link" href="#/lab/demo" onClick={enter('demo')} aria-label="进入演示查看质量门禁"><div className="landing-preview-heading"><Icon name="shield" size={16}/><span>质量门禁</span><span className="landing-preview-source">{DEEPSEEK_VERSION}</span></div><QualityGatePreview/><span className="landing-preview-action">进入实验工作台<SlideArrow/></span></a><figcaption>真实界面 · 演示快照 20260930-1958 · 非本次实时评测</figcaption></figure>
        </article>
        <EvidenceJourney enabled={motionEnabled}/>
        <article className="landing-proof-row landing-proof-reverse landing-proof-evidence">
          <div className="landing-proof-copy"><Icon name="trace" size={25}/><h3>再逐句看有没有据</h3><p>模型每说一句，旁边就是它引用的原文；没有引用的会被标出来。</p></div>
          <figure><a className="landing-proof-link" href="#/lab/demo" onClick={enter('demo')} aria-label="进入演示检查原始证据"><div className="landing-preview-heading"><Icon name="trace" size={16}/><span>逐句溯源</span><span className="landing-preview-source">原始记录</span></div><EvidenceTracePreview/><span className="landing-preview-action">进入实验工作台<SlideArrow/></span></a><figcaption>真实界面 · 同一演示快照中的逐句证据</figcaption></figure>
        </article>
      </section>
      <section className="landing-capabilities" id="platform" aria-labelledby="platform-title"><div className="landing-section-heading"><h2 id="platform-title">从选题到出报告，就四步。</h2></div><dl>{capabilities.map((item, index) => <div key={item.title}><dt><span className="landing-capability-node" aria-hidden="true"><Icon name={item.icon} size={22}/><span>{String(index + 1).padStart(2, '0')}</span></span><span>{item.title}</span></dt><dd>{item.description}</dd></div>)}</dl></section>
      <section className="landing-closing"><div><h2>一次真实评测，原样呈现。</h2><p>不用注册，打开就能逐句核对。</p></div><a className="btn btn-primary" href="#/lab/demo" onClick={enter('demo')}>查看演示<Icon name="arrow" size={17}/></a></section>
    </main>
    <footer className="landing-footer"><span>鉴真全域 VeriScope AI</span><span>逐句核对 / 证据溯源 / 质量门禁</span><span className="landing-footer-links"><a href={REPO_URL} target="_blank" rel="noopener noreferrer"><Icon name="link" size={14}/>Gitee 源码</a><a href={`${REPO_URL}/blob/master/LICENSE`} target="_blank" rel="noopener noreferrer">Apache License 2.0</a><a href="#/lab/workspace" onClick={enter('workspace')}>配置评测<Icon name="arrow" size={14}/></a></span></footer>
  </div>;
}
