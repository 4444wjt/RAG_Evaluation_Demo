import type { Ref } from 'react';
import productText from '../../../demo.txt?raw';
import depositText from '../../../scenarios/deposit_insurance.txt?raw';
import disclosureText from '../../../scenarios/information_disclosure.txt?raw';
import { Icon } from '../components/Icon';
import { labHash } from '../labRoute';

export type ExampleEvidence = 'trace' | 'gate' | 'heldout';

type SampleGuide = {
  name: string; kind: string; purpose: string; question: string; quote: string; location: string;
  check: string; text: string; source: string; example: ExampleEvidence; action: string; destination: string;
};

const SAMPLES: Record<string, SampleGuide> = {
  'demo.txt': {
    name: '产品知识问答', kind: '虚构示例', purpose: '体验怎样把模型回答拆成陈述，再逐句对照资料找依据。',
    question: '星云科技最新一轮B轮融资金额是多少？',
    quote: '星云科技已累计获得三轮融资，最新一轮B轮融资金额达5亿元人民币。',
    location: '第 1 段', check: '轮次与金额要有原文支持，不能添加原文没有的细节。', text: productText,
    source: '虚构企业与产品，仅用于演示，不是本平台的真实业绩。', example: 'trace', action: '看本例的逐句证据',
    destination: '打开已有快照：先看模型回答，再看逐句判定与引用。',
  },
  'scenarios/deposit_insurance.txt': {
    name: '存款保险', kind: '条例节选', purpose: '检查金融问答是否说准保障范围、偿付限额和适用条件。',
    question: '存款保险的最高偿付限额是多少？', quote: '存款保险实行限额偿付，最高偿付限额为人民币50万元。',
    location: '第五条', check: '核对金额与限制条件；不能把“同一存款人在同一家投保机构”说成“每个账户”。', text: depositText,
    source: '《存款保险条例》节选；仅核对当前提供的文本，不担保法规现行有效。', example: 'gate', action: '看本例的修复前后验证',
    destination: '定位 25 题修复前后的门禁对照；这是已有历史验证，不是本次运行。',
  },
  'scenarios/information_disclosure.txt': {
    name: '政府信息公开', kind: '条例节选', purpose: '检查政务问答有没有混淆补正、答复和征求意见的期限。',
    question: '不能当场答复的政府信息公开申请，一般应在多少个工作日内答复？',
    quote: '行政机关不能当场答复的，应当自收到申请之日起20个工作日内予以答复；',
    location: '第三十三条', check: '回答要保留“工作日”，不能把补正的 7 个工作日当成答复期限。', text: disclosureText,
    source: '《政府信息公开条例（2019年修订）》节选；仅核对当前提供的文本，不担保法规现行有效。',
    example: 'heldout', action: '看含本资料的留出题验证',
    destination: '定位“换一批新题再验证”：含本资料与存款保险两份法规，不计算 Ragas 门禁。',
  },
};

export function hasSampleGuide(path: string): boolean { return Object.hasOwn(SAMPLES, path); }

/** What the selected example is for, the passage it is checked against, and the one next action. */
export function SampleDocumentGuide({ path, readOnly, titleRef, onInspect, onConfigure }: {
  path: string; readOnly: boolean; titleRef?: Ref<HTMLHeadingElement>;
  onInspect?: (example: ExampleEvidence) => void; onConfigure: () => void;
}) {
  const sample = SAMPLES[path];
  if (!sample) return null;
  const view = sample.example === 'trace' ? 'trace' : 'fix';
  const action = readOnly
    ? onInspect ? <button type="button" className="btn btn-primary" onClick={() => onInspect(sample.example)}>{sample.action}<Icon name="arrow" size={16}/></button>
      : <a className="btn btn-primary" href={labHash(view, true)}>{sample.action}<Icon name="arrow" size={16}/></a>
    : <button type="button" className="btn btn-secondary" onClick={onConfigure}>继续选择模型<Icon name="arrow" size={16}/></button>;
  return <section className="workspace-sample-guide" aria-labelledby="workspace-sample-guide-title">
    <header className="workspace-sample-head">
      <p className="workspace-sample-selection"><Icon name="check" size={14}/>{`已选择 · ${sample.name}`}<span className="workspace-sample-kind">{sample.kind}</span></p>
      <h4 id="workspace-sample-guide-title" ref={titleRef} tabIndex={-1} className="workspace-focus-target">本资料能验证什么？</h4>
      <p className="workspace-sample-purpose">{sample.purpose}</p>
    </header>
    <div className="workspace-sample-body">
      <dl className="workspace-sample-question">
        <div><dt>可以核查的问题</dt><dd>{sample.question}</dd></div>
        <div><dt>核查要点</dt><dd className="workspace-sample-check">{sample.check}</dd></div>
      </dl>
      <figure className="workspace-sample-quote">
        <figcaption>{`对应原文 · ${sample.location}`}</figcaption>
        <blockquote>{sample.quote}</blockquote>
        <p className="workspace-sample-source">{sample.source}</p>
      </figure>
    </div>
    <details className="workspace-sample-document"><summary><Icon name="chevron" size={14}/>查看这份资料全文</summary>
      <div className="workspace-sample-text">{sample.text}</div></details>
    <div className="workspace-sample-next">
      {action}
      <p>{readOnly ? sample.destination : '资料已选好；接着选择模型与裁判，现在不会调用模型。'}</p>
    </div>
  </section>;
}
