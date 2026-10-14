import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { DATASETS, MODEL_COLORS, MODELS } from '../config';
import { Icon } from '../components/Icon';
import type { WorkspaceConfig } from '../state';
import { FileImportControl } from './FileImportControl';
import './workspace-inputs.css';

const SAMPLE_NOTES: Record<string, string> = {
  'demo.txt': '产品知识问答示例',
  'scenarios/deposit_insurance.txt': '存款保险条例节选',
  'scenarios/information_disclosure.txt': '政府信息公开条例节选',
};

export interface ConfigurationCheck {
  status: 'checking' | 'success' | 'error';
  message: string;
}

interface Props {
  config: WorkspaceConfig;
  onChange: (config: WorkspaceConfig) => void;
  onRun: () => void;
  running: boolean;
  readOnly?: boolean;
  hasLiveResult: boolean;
  error: string | null;
  configurationCheck: ConfigurationCheck | null;
}

function Stage({ number, name, children }: { number: string; name: string; children: React.ReactNode }) {
  return <section className="workspace-stage"><div className="stage-track"><span>{number}</span><strong>{name}</strong></div><div className="stage-content">{children}</div></section>;
}

export function WorkspaceView({ config, onChange, onRun, running, readOnly = false, hasLiveResult, error, configurationCheck }: Props) {
  const feedback = useRef<HTMLDivElement>(null);
  const checking = configurationCheck?.status === 'checking';
  const [documentSource, setDocumentSource] = useState<'example' | 'computer'>(() => config.docImport?.relative_path === config.docPath ? 'computer' : 'example');
  const [importBusy, setImportBusy] = useState(false);
  const lastExample = useRef<string>(DATASETS.some((item) => item.path === config.docPath) ? config.docPath : DATASETS[0].path);
  useEffect(() => {
    if (configurationCheck) {
      feedback.current?.scrollIntoView?.({ block: 'nearest' });
      feedback.current?.focus({ preventScroll: true });
    }
  }, [configurationCheck]);
  const update = (patch: Partial<WorkspaceConfig>) => onChange({ ...config, ...patch });
  const selectedImport = config.docImport?.relative_path === config.docPath ? config.docImport : undefined;
  const selectDocumentSource = (source: 'example' | 'computer') => {
    setDocumentSource(source);
    update({ docPath: source === 'example' ? lastExample.current : config.docImport?.relative_path ?? '' });
  };
  const inputReady = Boolean(config.docPath.trim()) && (config.testsetMode !== 'custom' || Boolean(config.testsetPath.trim()));
  const toggleModel = (id: string) => update({ modelNames: config.modelNames.includes(id) ? config.modelNames.filter((model) => model !== id) : [...config.modelNames, id] });
  return <div className="experiment-workspace">
    <div className="workspace-heading"><div><h2>评测配置</h2><p>先选示例或导入资料，再配置模型并运行。</p></div>{!readOnly && <span className="badge neutral">本地评测</span>}</div>
    {!readOnly && <p className="workspace-start-hint"><Icon name="play" size={16}/><span>查看已有结果，点击页头「加载演示」；运行新评测，从下方选择资料。</span></p>}
    {readOnly && <div className="readonly-notice" role="note"><Icon name="shield" size={16}/><p><strong>公开演示 · 只读</strong>可以浏览全部配置项；公开站点不会调用模型，在线评测请按仓库 README 在本地运行。</p></div>}
    <fieldset disabled={running || checking || importBusy} className="workspace-fields">
      <legend className="sr-only">评测配置</legend>
      <Stage number="01" name="数据">
        <div className="stage-heading"><h3>数据输入</h3><p>选择项目自带示例，或从电脑导入资料。</p></div>
        <div className="dataset-grid">
          <div className="workspace-field workspace-input-panel"><h4 className="field-label"><Icon name="folder"/>评测资料</h4>
            <div className="workspace-source-switch" role="group" aria-label="评测资料来源">
              <button type="button" aria-pressed={documentSource === 'example'} onClick={() => selectDocumentSource('example')}><Icon name="folder" size={17}/>示例资料</button>
              <button type="button" aria-pressed={documentSource === 'computer'} onClick={() => selectDocumentSource('computer')}><Icon name="upload" size={17}/>电脑文件</button>
            </div>
            {documentSource === 'example' ? <>
              <div className="workspace-example-tree">
                <div className="workspace-tree-root"><Icon name="folder" size={17}/><strong>项目自带资料</strong><span>{DATASETS.length} 份</span></div>
                <div className="workspace-tree-files" role="radiogroup" aria-label="示例评测资料">
                  {DATASETS.map((item) => <label className={`workspace-example-choice${config.docPath === item.path ? ' is-selected' : ''}`} key={item.path}>
                    <input type="radio" name="workspace-document-example" value={item.path} checked={config.docPath === item.path}
                      onChange={() => { lastExample.current = item.path; update({ docPath: item.path }); }}/>
                    <Icon name="file" size={18}/><span><strong>{item.label}</strong><small>{SAMPLE_NOTES[item.path]}</small></span>
                    {config.docPath === item.path && <Icon name="check" size={17}/>}</label>)}
                </div>
              </div>
              <p className="workspace-input-hint">示例已随项目提供，直接选择即可，无需手动放置文件。</p>
            </> : <FileImportControl kind="document" imported={selectedImport} disabled={running || checking || importBusy} readOnly={readOnly}
              onBusy={setImportBusy} onImported={(input) => update({ docPath: input.relative_path, docImport: input })}/>}
            <details className="workspace-input-advanced"><summary>项目内路径（高级）<Icon name="chevron" size={14}/></summary>
              <p>仅用于文件已经保存在后端项目中的情况；电脑文件请使用上方导入入口。</p>
              <label htmlFor="doc-path">评测文档相对路径</label><input id="doc-path" value={config.docPath} onChange={(event) => update({ docPath: event.target.value, docImport: undefined })} placeholder="scenarios/your-document.txt" autoComplete="off" spellCheck={false}/>
            </details>
          </div>
          <div className="workspace-field workspace-input-panel"><label className="field-label" htmlFor="testset-mode"><Icon name="layers"/>测试集来源</label>
            <select id="testset-mode" value={config.testsetMode} onChange={(event) => update({ testsetMode: event.target.value as WorkspaceConfig['testsetMode'] })}>
              <option value="auto">基于文档自动生成</option><option value="custom">已有自定义测试集</option><option value="benchmark">TruthfulQA 基准</option>
            </select>
            {config.testsetMode === 'custom' ? <>
              <button type="button" className="workspace-testset-example" aria-pressed={config.testsetPath === 'testset_example.csv'} onClick={() => update({ testsetPath: 'testset_example.csv', testsetImport: undefined })}><Icon name="file" size={17}/><span>使用示例测试集<small>testset_example.csv</small></span>{config.testsetPath === 'testset_example.csv' && <Icon name="check" size={17}/>}</button>
              <FileImportControl kind="testset" imported={config.testsetImport?.relative_path === config.testsetPath ? config.testsetImport : undefined}
                disabled={running || checking || importBusy} readOnly={readOnly} onBusy={setImportBusy}
                onImported={(input) => update({ testsetPath: input.relative_path, testsetImport: input })}/>
              <details className="workspace-input-advanced"><summary>测试集路径（高级）<Icon name="chevron" size={14}/></summary>
                <label htmlFor="testset-path">测试集相对路径</label><input id="testset-path" value={config.testsetPath} onChange={(event) => update({ testsetPath: event.target.value, testsetImport: undefined })} placeholder="scenarios/your-testset.json" spellCheck={false}/>
              </details>
            </> : config.testsetMode === 'benchmark' ? <><label className="inline-label" htmlFor="benchmark-limit">基准样本数量</label><input id="benchmark-limit" type="number" min={1} max={50} step={1} value={Number.isFinite(config.benchmarkLimit) ? config.benchmarkLimit : ''} onChange={(event) => update({ benchmarkLimit: event.currentTarget.valueAsNumber })}/></> : <div className="workspace-testset-guidance"><Icon name="layers" size={22}/><h4>不必提前准备题目</h4><p>开始评测后，系统会根据所选资料生成问题和参考答案。</p><small>已有固定题集？切换为「已有自定义测试集」即可导入。</small></div>}
            <p className="workspace-input-note"><Icon name="shield" size={15}/><span>{readOnly ? '当前为只读演示，不会上传资料或调用模型。' : '导入资料不产生模型费用；点击「开始评测」后才会调用模型。'}</span></p>
          </div>
        </div>
      </Stage>
      <Stage number="02" name="模型">
        <div className="stage-heading"><div><h3>模型选择</h3><p>选择本次参与评测的模型，支持多选。</p></div><span className="muted selection-count">已选择 {config.modelNames.length} 个</span></div>
        <div className="model-grid">{MODELS.map((model, index) => {
          const selected = config.modelNames.includes(model.id);
          return <button type="button" className={`model-card ${selected ? 'selected' : ''}`} aria-pressed={selected} aria-label={`选择 ${model.label}`} key={model.id} onClick={() => toggleModel(model.id)} style={{ '--model-color': MODEL_COLORS[index % MODEL_COLORS.length] } as CSSProperties}>
            <span className="model-card-top">
              <span className="model-identity"><span className="model-swatch" aria-hidden="true"/><strong title={model.label}>{model.label}</strong></span>
              <span className="model-status"><span className={`model-check ${selected ? 'checked' : ''}`} aria-hidden="true">{selected && <Icon name="check" size={13}/>}</span><span className="model-status-text" key={selected ? 'on' : 'off'}>{selected ? '已选择' : '可选择'}</span></span>
            </span>
            <span className="model-detail">{model.detail}</span>
          </button>;
        })}</div>
        <small className="stage-footnote">名称须与本地后端配置一致；选择状态不代表模型服务已验证可用。</small>
      </Stage>
      <Stage number="03" name="参数">
        <div className="stage-heading"><div><h3>评测参数</h3><p>质量门禁沿用原有正向用例判定。</p></div><label className="checkbox-label"><input type="checkbox" checked={config.useDefaultThresholds} onChange={(event) => update({ useDefaultThresholds: event.target.checked })}/>沿用后端默认阈值</label></div>
        <div className="parameter-grid">{[
          { key: 'faithThreshold' as const, label: '忠实度门禁阈值', note: '用于判断回答是否忠实于检索上下文。' },
          { key: 'contextThreshold' as const, label: '上下文精度门禁阈值', note: '用于判断检索上下文是否与问题相关。' },
        ].map((parameter) => <div className="workspace-field parameter-control" key={parameter.key}>
          <div className="parameter-heading"><label htmlFor={parameter.key}>{parameter.label}</label>{config.useDefaultThresholds ? <span className="badge neutral">后端默认</span> : <div className="parameter-value"><input aria-label={parameter.label} id={parameter.key} type="number" min={0} max={1} step={0.05} value={Number.isFinite(config[parameter.key]) ? config[parameter.key] : ''} onChange={(event) => update({ [parameter.key]: event.currentTarget.valueAsNumber })}/><button className="reset-button" type="button" aria-label={`重置${parameter.label}`} title="恢复手动预设 0.8" onClick={() => update({ [parameter.key]: 0.8 })}><Icon name="refresh" size={14}/></button></div>}</div>
          <p>{parameter.note}</p>
          {config.useDefaultThresholds ? <span className="parameter-default">实际阈值由服务端配置提供</span> : <input type="range" aria-label={`${parameter.label}滑条`} min={0} max={1} step={0.05} value={Number.isFinite(config[parameter.key]) ? config[parameter.key] : 0} onChange={(event) => update({ [parameter.key]: event.currentTarget.valueAsNumber })}/>}
        </div>)}</div>
      </Stage>
      <Stage number="04" name="运行">
        {configurationCheck && <div ref={feedback} className={`configuration-feedback ${configurationCheck.status}`} role="status" aria-label="配置检查结果" tabIndex={-1}><Icon name={checking ? 'refresh' : configurationCheck.status === 'success' ? 'check' : 'alert'} size={18}/><div><strong>本地参数与服务检查</strong><p>{configurationCheck.message}</p><small>此检查不会运行评测，也不会验证文件是否存在、模型或凭据是否可用。</small></div></div>}
        <div className="run-area"><div><h3>执行评测</h3><p>{readOnly ? '公开站点仅提供演示快照，不会调用模型。' : running ? '评测正在运行，请保持当前页面打开。' : !inputReady ? '请先选择示例资料，或完成电脑文件导入。' : '执行当前配置，完成后查看门禁、模型指标和逐句证据。'}</p><small>{readOnly ? '在线评测请按仓库 README 在本地运行。' : '真实评测需先在本地后端配置模型凭据。'}</small></div><button type="button" className="btn btn-primary run-button" onClick={onRun} disabled={readOnly || running || checking || importBusy || !inputReady}><Icon name={running ? 'refresh' : 'play'}/>{running ? '评测中' : hasLiveResult ? '再次评测' : '开始评测'}</button></div>
        {error && <p className="form-error" role="alert">{error}</p>}
      </Stage>
    </fieldset>
  </div>;
}
