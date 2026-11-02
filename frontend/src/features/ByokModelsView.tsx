import { useState } from 'react';
import type { FormEvent } from 'react';
import { BYOK_PROVIDERS, MAX_BYOK_MODELS, byokEntries, removeByokModel, saveByokModel } from '../api/byok';
import type { ByokModel } from '../api/byok';
import type { ModelEntry } from '../api/models';
import { Icon } from '../components/Icon';

const empty = (): ByokModel => ({ name: 'deepseek-chat', base_url: 'https://api.deepseek.com', api_key: '' });

export function ByokModelsView({ onModelsChange }: { onModelsChange?: (models: ModelEntry[]) => void }) {
  const [models, setModels] = useState(byokEntries);
  const [draft, setDraft] = useState<ByokModel | null>(null);
  const [editing, setEditing] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [provider, setProvider] = useState('https://api.deepseek.com');
  const update = (next: ModelEntry[]) => { setModels(next); onModelsChange?.(next); };
  const open = (model?: ModelEntry) => {
    const base = model?.baseUrl ?? 'https://api.deepseek.com';
    setProvider(BYOK_PROVIDERS.some((entry) => entry.base_url === base) ? base : 'custom');
    setDraft(model ? { name: model.name, base_url: base, api_key: '' } : empty());
    setEditing(model?.name); setError(null); setNotice(null);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!draft) return;
    try {
      update(saveByokModel(draft, editing));
      setNotice('已保存到本页内存，尚未验证接口可用。请返回工作区选择模型并开始评测。');
      setDraft(null); setEditing(undefined); setError(null);
    } catch (problem) { setError(problem instanceof Error ? problem.message : '配置未能保存。'); }
  };
  return <div className="audit-view mc-view">
    <section className="audit-panel" aria-labelledby="byok-title">
      <div className="audit-panel-head"><div><h2 id="byok-title">使用自己的模型</h2><p>填写 OpenAI 兼容接口和自己的 API Key，再回到工作区选择模型、开始评测。</p></div>
        {!draft && <button type="button" className="btn btn-primary" disabled={models.length >= MAX_BYOK_MODELS} onClick={() => open()}><Icon name="layers" size={15}/>配置模型</button>}</div>
      <p className="audit-note">最多 3 个模型（含独立裁判）。运行时由本站后端向所选服务发起请求，费用由所选服务收取。密钥仅保存在当前页面内存，不写入浏览器存储、历史或下载包，刷新后需重填；资料与历史在会话闲置24小时后清理，请及时下载。</p>
      {notice && <p className="mc-notice" role="status">{notice}</p>}
      {models.length > 0 ? <div className="audit-table-scroll"><table className="mc-table" aria-label="自己的云端模型"><thead><tr><th scope="col">模型名</th><th scope="col">接口地址</th><th scope="col">密钥</th><th scope="col">操作</th></tr></thead><tbody>{models.map((model) => <tr key={model.name}><th scope="row">{model.name}</th><td><code>{model.baseUrl}</code></td><td><span className="audit-chip audit-chip--success">已填写</span></td><td className="mc-actions"><button type="button" className="btn btn-ghost" disabled={Boolean(draft)} onClick={() => open(model)} aria-label={`编辑 ${model.name}`}>编辑</button><button type="button" className="btn btn-ghost" disabled={Boolean(draft)} onClick={() => { update(removeByokModel(model.name)); setNotice('已从本页移除该模型与密钥。'); }} aria-label={`移除 ${model.name}`}>移除</button></td></tr>)}</tbody></table></div> : !draft && <p className="audit-note">还没有配置模型。可先选择 DeepSeek 或阿里云百炼，再填写自己的密钥。</p>}
      {draft && <form className="mc-form" aria-label={editing ? '编辑云端模型' : '配置云端模型'} onSubmit={submit} noValidate><fieldset><legend>{editing ? `编辑 ${editing}（请重新填写密钥）` : '配置云端模型'}</legend>
        <div className="mc-field"><label htmlFor="byok-provider">接口服务</label><select id="byok-provider" value={provider} onChange={(event) => { const value = event.currentTarget.value; setProvider(value); const selected = BYOK_PROVIDERS.find((entry) => entry.base_url === value); setDraft({ ...draft, base_url: selected?.base_url ?? '', name: selected?.name ?? '' }); }}>
          {BYOK_PROVIDERS.map((entry) => <option value={entry.base_url} key={entry.label}>{entry.label}</option>)}<option value="custom">自定义接口</option></select></div>
        <div className="mc-field"><label htmlFor="byok-name">模型名</label><input id="byok-name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.currentTarget.value })} autoComplete="off" spellCheck={false}/><small>填写接口请求使用的模型名，例如 deepseek-chat 或 qwen-flash。</small></div>
        <div className="mc-field"><label htmlFor="byok-url">接口地址</label><input id="byok-url" value={draft.base_url} onChange={(event) => { setProvider('custom'); setDraft({ ...draft, base_url: event.currentTarget.value }); }} autoComplete="off" spellCheck={false}/><small>自定义 HTTPS 地址需由管理员加入服务器允许列表。</small></div>
        <div className="mc-field"><label htmlFor="byok-key">API Key</label><input id="byok-key" type="password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.currentTarget.value })} autoComplete="off" spellCheck={false}/><small>请使用你自己的 API Key。</small></div>
        {error && <p className="mc-error" role="alert">{error}</p>}
        <div className="mc-form-actions"><button type="submit" className="btn btn-primary">保存到本页</button><button type="button" className="btn btn-secondary" onClick={() => { setDraft(null); setEditing(undefined); setError(null); }}>取消</button></div>
      </fieldset></form>}
    </section>
  </div>;
}
