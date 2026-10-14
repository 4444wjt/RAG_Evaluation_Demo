import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { fetchModels, MAX_LOCAL_MODELS, MODEL_CONFIG_UNAVAILABLE, saveLocalModels, suggestKeyEnv, toInput, validateLocalModel } from '../api/models';
import type { LocalModelInput, ModelEntry } from '../api/models';
import { Icon } from '../components/Icon';
import { READ_ONLY, REPO_URL, STATIC_SITE } from '../config';
import './gate-audit.css';
import './models.css';

const GUIDE_URL = `${REPO_URL}/blob/master/docs/DEPLOYMENT.md#接入自有模型`;
const EMPTY: LocalModelInput = { name: '', display_name: '', base_url: '', api_key_env: '' };
const FIELDS: { key: keyof LocalModelInput; label: string; placeholder: string; hint: React.ReactNode }[] = [
  { key: 'name', label: '模型名', placeholder: 'qwen2.5-7b', hint: '请求接口时使用的名称，与服务端一致。' },
  { key: 'display_name', label: '显示名', placeholder: '内网 Qwen2.5-7B', hint: '可留空，留空时显示模型名。' },
  { key: 'base_url', label: '接口地址', placeholder: 'http://127.0.0.1:11434/v1', hint: 'OpenAI 兼容地址，通常以 /v1 结尾。' },
  { key: 'api_key_env', label: '密钥变量名', placeholder: 'VERISCOPE_QWEN_API_KEY', hint: <>只填变量名，密钥写在 <code>.env</code>。接口不需要密钥时（如 Ollama），给变量随便写一个值。</> },
];

function KeyStatus({ model }: { model: ModelEntry }) {
  return model.keyConfigured
    ? <span className="audit-chip audit-chip--success">已设置</span>
    : <span className="audit-chip audit-chip--warning" title={`在 .env 中写入 ${model.apiKeyEnv}=…，重启后端后生效`}>未设置</span>;
}

function ModelTable({ models, label, actions }: { models: ModelEntry[]; label: string; actions?: (model: ModelEntry) => React.ReactNode }) {
  return <div className="audit-table-scroll"><table className="mc-table" aria-label={label}>
    <thead><tr><th scope="col">显示名</th><th scope="col">模型名</th><th scope="col">接口地址</th><th scope="col">密钥变量</th><th scope="col">密钥</th>{actions && <th scope="col"><span className="sr-only">操作</span></th>}</tr></thead>
    <tbody>{models.map((model) => <tr key={model.name}>
      <th scope="row">{model.displayName}</th><td><code>{model.name}</code></td><td><code>{model.baseUrl}</code></td><td><code>{model.apiKeyEnv}</code></td><td><KeyStatus model={model}/></td>
      {actions && <td className="mc-actions">{actions(model)}</td>}
    </tr>)}</tbody>
  </table></div>;
}

/** Register OpenAI-compatible models on this machine. Keys stay in .env; this page only records which variable holds each one. */
export function ModelsView({ onModelsChange }: { onModelsChange?: (models: ModelEntry[]) => void }) {
  const unavailable = READ_ONLY || STATIC_SITE;
  const [models, setModels] = useState<ModelEntry[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<LocalModelInput | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [keyEnvTouched, setKeyEnvTouched] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const report = useRef(onModelsChange);
  report.current = onModelsChange;

  const load = (signal?: AbortSignal) => {
    setLoadError(null);
    fetchModels(signal).then((list) => { setModels(list); report.current?.(list); }).catch((error: unknown) => {
      if (!signal?.aborted) setLoadError(error instanceof Error ? error.message : '模型列表未能读取。');
    });
  };
  useEffect(() => {
    if (unavailable) return;
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [unavailable]);

  if (unavailable) return <div className="audit-view mc-view">
    <section className="audit-panel" role="note"><div className="audit-panel-head"><div><h2>模型配置</h2><p>{MODEL_CONFIG_UNAVAILABLE}</p></div></div>
      <p className="audit-note">本地运行时，这里可以登记任何 OpenAI 兼容接口的模型（vLLM、Ollama、内部网关等）。密钥只写在本机 <code>.env</code> 中，页面不接收密钥。</p>
      <a className="audit-source" href={GUIDE_URL} target="_blank" rel="noopener noreferrer"><Icon name="link" size={13}/>接入自有模型的步骤</a>
    </section>
  </div>;

  const builtin = models?.filter((model) => model.source === 'builtin') ?? [];
  const local = models?.filter((model) => model.source === 'local') ?? [];

  const persist = async (next: LocalModelInput[], done: string) => {
    setSaving(true);
    setFormError(null);
    setNotice(null);
    try {
      const list = await saveLocalModels(next);
      setModels(list);
      onModelsChange?.(list);
      setDraft(null);
      setEditing(null);
      setNotice(done);
      return true;
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '模型登记未能保存。');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const open = (model?: ModelEntry) => {
    setDraft(model ? toInput(model) : { ...EMPTY });
    setEditing(model?.name ?? null);
    setKeyEnvTouched(Boolean(model));
    setFormError(null);
    setNotice(null);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!draft || !models) return;
    const taken = models.filter((model) => model.name !== editing).map((model) => model.name);
    const problem = validateLocalModel(draft, taken);
    if (problem) { setFormError(problem); return; }
    const clean = { name: draft.name.trim(), display_name: draft.display_name.trim(), base_url: draft.base_url.trim(), api_key_env: draft.api_key_env.trim() };
    const next = editing ? local.map((model) => model.name === editing ? clean : toInput(model)) : [...local.map(toInput), clean];
    void persist(next, `已保存 ${clean.display_name || clean.name}。在评测工作区的「模型」一步可以选中它。`);
  };

  const remove = (model: ModelEntry) => {
    void persist(local.filter((item) => item.name !== model.name).map(toInput), `已移除 ${model.displayName}。.env 中的密钥没有改动。`);
  };

  const change = (field: keyof LocalModelInput, value: string) => {
    if (!draft) return;
    const next = { ...draft, [field]: value };
    if (field === 'name' && !keyEnvTouched) next.api_key_env = suggestKeyEnv(value);
    if (field === 'api_key_env') setKeyEnvTouched(true);
    setDraft(next);
  };

  return <div className="audit-view mc-view">
    <section className="audit-subject mc-steps" aria-label="接入步骤">
      <ol>
        <li><strong>准备接口</strong><span>任何 OpenAI 兼容地址：vLLM、Ollama、内部网关。</span></li>
        <li><strong>写入密钥</strong><span>在项目根目录 <code>.env</code> 写 <code>VERISCOPE_&lt;名称&gt;_API_KEY=…</code>，重启后端。</span></li>
        <li><strong>在下方登记</strong><span>只填模型名、地址和密钥变量名，页面不接收密钥。</span></li>
        <li><strong>工作区试跑</strong><span>在评测工作区选中新模型，先用少量题目试跑。</span></li>
      </ol>
      <a className="audit-source" href={GUIDE_URL} target="_blank" rel="noopener noreferrer"><Icon name="link" size={13}/>完整步骤</a>
    </section>

    {loadError && <div className="error-notice" role="alert"><Icon name="alert"/><div><strong>模型列表未能读取</strong><p>{loadError}</p></div><button className="btn btn-ghost" onClick={() => load()}>重试<Icon name="refresh" size={14}/></button></div>}
    {!models && !loadError && <p className="audit-note" role="status">正在读取模型列表…</p>}

    {models && <>
      <section className="audit-panel" aria-labelledby="mc-builtin-title">
        <div className="audit-panel-head"><div><h2 id="mc-builtin-title">内置模型</h2><p>来自 <code>backend/config.py</code>，地址可用环境变量覆盖，这里只读。</p></div></div>
        <ModelTable models={builtin} label="内置模型"/>
      </section>

      <section className="audit-panel" aria-labelledby="mc-local-title">
        <div className="audit-panel-head"><div><h2 id="mc-local-title">本机登记的模型</h2><p>保存在项目根目录 <code>models.local.json</code>，已被 git 忽略，不会入库。</p></div>
          {!draft && <button className="btn btn-primary" onClick={() => open()} disabled={saving || local.length >= MAX_LOCAL_MODELS}><Icon name="layers" size={15}/>登记模型</button>}</div>
        {notice && <p className="mc-notice" role="status"><Icon name="check" size={14}/>{notice}</p>}
        {local.length ? <ModelTable models={local} label="本机登记的模型" actions={(model) => <>
          <button className="btn btn-ghost" onClick={() => open(model)} disabled={saving || Boolean(draft)} aria-label={`编辑 ${model.displayName}`}>编辑</button>
          <button className="btn btn-ghost" onClick={() => remove(model)} disabled={saving || Boolean(draft)} aria-label={`移除 ${model.displayName}`}>移除</button>
        </>}/> : !draft && <p className="audit-note">还没有登记模型。</p>}

        {draft && <form className="mc-form" onSubmit={submit} aria-label={editing ? '编辑模型' : '登记模型'} noValidate>
          <fieldset disabled={saving}>
            <legend>{editing ? `编辑 ${editing}` : '登记模型'}</legend>
            {FIELDS.map((field) => <div className="mc-field" key={field.key}>
              <label htmlFor={`mc-${field.key}`}>{field.label}</label>
              <input id={`mc-${field.key}`} aria-describedby={`mc-${field.key}-hint`} value={draft[field.key]} onChange={(event) => change(field.key, event.currentTarget.value)} placeholder={field.placeholder} autoComplete="off" spellCheck={false} required={field.key !== 'display_name'}/>
              <small id={`mc-${field.key}-hint`}>{field.hint}</small>
            </div>)}
            {formError && <p className="mc-error" role="alert">{formError}</p>}
            <div className="mc-form-actions"><button type="submit" className="btn btn-primary">{saving ? '保存中…' : '保存'}</button><button type="button" className="btn btn-secondary" onClick={() => { setDraft(null); setEditing(null); setFormError(null); }}>取消</button></div>
          </fieldset>
        </form>}
        {!draft && formError && <p className="mc-error" role="alert">{formError}</p>}
      </section>
    </>}
  </div>;
}
