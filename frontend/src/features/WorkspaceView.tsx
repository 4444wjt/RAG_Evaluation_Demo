import { useEffect, useRef } from 'react';
import { DATASETS, MODELS } from '../config';
import { Icon } from '../components/Icon';
import type { WorkspaceConfig } from '../state';

export interface ConfigurationCheck {
  status: 'checking' | 'success' | 'error';
  message: string;
}

interface Props {
  config: WorkspaceConfig;
  onChange: (config: WorkspaceConfig) => void;
  onRun: () => void;
  running: boolean;
  hasLiveResult: boolean;
  error: string | null;
  configurationCheck: ConfigurationCheck | null;
}

function Stage({ number, name, children }: { number: string; name: string; children: React.ReactNode }) {
  return <section className="workspace-stage"><div className="stage-track"><span>{number}</span><strong>{name}</strong></div><div className="stage-content">{children}</div></section>;
}

export function WorkspaceView({ config, onChange, onRun, running, hasLiveResult, error, configurationCheck }: Props) {
  const feedback = useRef<HTMLDivElement>(null);
  const checking = configurationCheck?.status === 'checking';
  useEffect(() => {
    if (configurationCheck) {
      feedback.current?.scrollIntoView?.({ block: 'nearest' });
      feedback.current?.focus({ preventScroll: true });
    }
  }, [configurationCheck]);
  const update = (patch: Partial<WorkspaceConfig>) => onChange({ ...config, ...patch });
  const toggleModel = (id: string) => update({ modelNames: config.modelNames.includes(id) ? config.modelNames.filter((model) => model !== id) : [...config.modelNames, id] });
  return <div className="experiment-workspace">
    <div className="workspace-heading"><div><h2>Experiment Workspace</h2><p>配置实验，验证模型回答的可靠性</p></div><span className="badge neutral">本地评测</span></div>
    <fieldset disabled={running || checking} className="workspace-fields">
      <legend className="sr-only">评测配置</legend>
      <Stage number="01" name="Dataset">
        <div className="stage-heading"><h3>数据输入</h3><p>使用已放入项目目录的文档；自定义测试集可选。</p></div>
        <div className="dataset-grid">
          <div className="control-surface"><label className="field-label" htmlFor="dataset-preset"><Icon name="folder"/>评测文档</label>
            <select id="dataset-preset" value={DATASETS.some((item) => item.path === config.docPath) ? config.docPath : '__custom__'} onChange={(event) => update({ docPath: event.target.value === '__custom__' ? '' : event.target.value })}>
              {DATASETS.map((item) => <option key={item.path} value={item.path}>{item.label}</option>)}<option value="__custom__">其他项目内文档</option>
            </select>
            <label className="sr-only" htmlFor="doc-path">评测文档相对路径</label><input id="doc-path" value={config.docPath} onChange={(event) => update({ docPath: event.target.value })} placeholder="demo.txt 或 scenarios/ 下的文档" autoComplete="off" spellCheck={false}/>
            <small>TXT / PDF / DOCX / MD · 路径由服务端检查</small>
          </div>
          <div className="control-surface"><label className="field-label" htmlFor="testset-mode"><Icon name="layers"/>测试集来源</label>
            <select id="testset-mode" value={config.testsetMode} onChange={(event) => update({ testsetMode: event.target.value as WorkspaceConfig['testsetMode'] })}>
              <option value="auto">基于文档自动生成</option><option value="custom">已有自定义测试集</option><option value="benchmark">TruthfulQA 基准</option>
            </select>
            {config.testsetMode === 'custom' ? <><label className="sr-only" htmlFor="testset-path">测试集相对路径</label><input id="testset-path" value={config.testsetPath} onChange={(event) => update({ testsetPath: event.target.value })} placeholder="testset_example.csv" spellCheck={false}/><small>JSON / CSV / TXT · 使用已有项目文件</small></> : config.testsetMode === 'benchmark' ? <><label className="inline-label" htmlFor="benchmark-limit">基准样本数量</label><input id="benchmark-limit" type="number" min={1} step={1} value={Number.isFinite(config.benchmarkLimit) ? config.benchmarkLimit : ''} onChange={(event) => update({ benchmarkLimit: event.currentTarget.valueAsNumber })}/></> : <p className="control-description">由现有评测引擎生成正向与对抗性测试用例，无需额外准备测试集。</p>}
          </div>
        </div>
      </Stage>
      <Stage number="02" name="Models">
        <div className="stage-heading"><div><h3>模型选择</h3><p>选择本次参与评测的模型，支持多选。</p></div><span className="muted selection-count">已选择 {config.modelNames.length} 个</span></div>
        <div className="model-grid">{MODELS.map((model) => {
          const selected = config.modelNames.includes(model.id);
          return <button type="button" className={`model-card ${selected ? 'selected' : ''}`} aria-pressed={selected} aria-label={`选择 ${model.label}`} key={model.id} onClick={() => toggleModel(model.id)}>
            <span className="model-card-top"><span className="model-identity"><span className="model-avatar">{model.initials}</span><strong>{model.label}</strong></span><span className="model-status"><span className={`model-check ${selected ? 'checked' : ''}`} aria-hidden="true">{selected && <Icon name="check" size={13}/>}</span>{selected ? '已选择' : '可选择'}</span></span>
            <span className="model-detail">{model.detail}</span>
          </button>;
        })}</div>
        <small className="stage-footnote">名称须与本地后端配置一致；选择状态不代表模型服务已验证可用。</small>
      </Stage>
      <Stage number="03" name="Evaluation">
        <div className="stage-heading"><div><h3>评测参数</h3><p>质量门禁沿用原有正向用例判定。</p></div><label className="checkbox-label"><input type="checkbox" checked={config.useDefaultThresholds} onChange={(event) => update({ useDefaultThresholds: event.target.checked })}/>沿用后端默认阈值</label></div>
        <div className="parameter-grid">{[
          { key: 'faithThreshold' as const, label: '忠实度门禁阈值', note: '用于判断回答是否忠实于检索上下文。' },
          { key: 'contextThreshold' as const, label: '上下文精度门禁阈值', note: '用于判断检索上下文是否与问题相关。' },
        ].map((parameter) => <div className="control-surface parameter-control" key={parameter.key}>
          <div className="parameter-heading"><label htmlFor={parameter.key}>{parameter.label}</label>{config.useDefaultThresholds ? <span className="badge neutral">后端默认</span> : <div className="parameter-value"><input aria-label={parameter.label} id={parameter.key} type="number" min={0} max={1} step={0.05} value={Number.isFinite(config[parameter.key]) ? config[parameter.key] : ''} onChange={(event) => update({ [parameter.key]: event.currentTarget.valueAsNumber })}/><button className="reset-button" type="button" aria-label={`重置${parameter.label}`} title="恢复手动预设 0.8" onClick={() => update({ [parameter.key]: 0.8 })}><Icon name="refresh" size={14}/></button></div>}</div>
          <p>{parameter.note}</p>
          {config.useDefaultThresholds ? <span className="parameter-default">实际阈值由服务端配置提供</span> : <input type="range" aria-label={`${parameter.label}滑条`} min={0} max={1} step={0.05} value={Number.isFinite(config[parameter.key]) ? config[parameter.key] : 0} onChange={(event) => update({ [parameter.key]: event.currentTarget.valueAsNumber })}/>}
        </div>)}</div>
      </Stage>
      <Stage number="04" name="Run">
        {configurationCheck && <div ref={feedback} className={`configuration-feedback ${configurationCheck.status}`} role="status" aria-label="配置检查结果" tabIndex={-1}><Icon name={checking ? 'refresh' : configurationCheck.status === 'success' ? 'check' : 'alert'} size={18}/><div><strong>本地参数与服务检查</strong><p>{configurationCheck.message}</p><small>此检查不会运行评测，也不会验证文件是否存在、模型或凭据是否可用。</small></div></div>}
        <div className="run-area"><div><h3>执行评测</h3><p>{running ? '评测正在运行，请保持当前页面打开。' : '执行当前配置，完成后查看门禁、模型指标和逐句证据。'}</p><small>同一时间运行一次评测；与 Gradio / CLI 交替使用。</small></div><button type="button" className="btn btn-primary run-button" onClick={onRun} disabled={running || checking}><Icon name={running ? 'refresh' : 'play'}/>{running ? '评测中' : hasLiveResult ? '再次评测' : '开始评测'}</button></div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </Stage>
    </fieldset>
  </div>;
}
