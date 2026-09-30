import json
from collections import Counter, defaultdict

data = json.load(open(r'D:\RAG_Evaluation_Demo\eval_history.json', encoding='utf-8-sig'))

print("总记录数:", len(data))
print()

# 按 testset_source 分类
src_counter = Counter(r.get('testset_source', '<空>') for r in data)
print("testset_source 分布:")
for k, v in src_counter.items():
    print(f"  {k!r}: {v}")
print()

demo_src = "内置演示数据"
real_runs = [r for r in data if r.get('testset_source') != demo_src]
demo_runs = [r for r in data if r.get('testset_source') == demo_src]
print(f"真实评测记录: {len(real_runs)} | 演示记录: {len(demo_runs)}")
print()

def models_of(runs):
    return [m for r in runs for m in (r.get('models') or []) if isinstance(m, dict)]

allm = models_of(data)
realm = models_of(real_runs)
demom = models_of(demo_runs)
print(f"模型记录总数: {len(allm)} | 真实: {len(realm)} | 演示: {len(demom)}")
print()

print("=" * 70)
print("一、真实评测（非演示）模型记录的字段情况")
print("=" * 70)

has_key = sum('refusal_accuracy' in m for m in realm)
no_key = sum('refusal_accuracy' not in m for m in realm)
print(f"含 'refusal_accuracy' 键: {has_key}")
print(f"缺 'refusal_accuracy' 键: {no_key}")

val_breakdown = Counter()
for m in realm:
    if 'refusal_accuracy' not in m:
        val_breakdown['<缺键>'] += 1
    else:
        v = m['refusal_accuracy']
        if v is None:
            val_breakdown['None'] += 1
        elif isinstance(v, float) and v != v:
            val_breakdown['NaN'] += 1
        elif v == "":
            val_breakdown['空字符串'] += 1
        else:
            val_breakdown['数字'] += 1
print("值分布:", dict(val_breakdown))

# 负样本信号：neg_faithfulness / neg_context_precision 任一非 None 即视为"存在负样本"
neg_signal = 0
neg_valued = 0
for m in realm:
    nf = m.get('neg_faithfulness')
    nc = m.get('neg_context_precision')
    has_signal = (nf is not None) or (nc is not None)
    # NaN 也视为 None（无有效值）
    if isinstance(nf, float) and nf != nf:
        nf = None
    if isinstance(nc, float) and nc != nc:
        nc = None
    if (nf is not None) or (nc is not None):
        neg_signal += 1
print(f"存在负样本信号（neg_faithfulness 或 neg_context_precision 非空）: {neg_signal}")
print()

print("=" * 70)
print("二、逐条真实评测明细（负样本信号 vs refusal_accuracy）")
print("=" * 70)
for r in real_runs:
    ts = r.get('timestamp', '?')
    ver = r.get('version', '?')
    src = r.get('testset_source', '?')
    for m in (r.get('models') or []):
        if not isinstance(m, dict):
            continue
        nf = m.get('neg_faithfulness')
        nc = m.get('neg_context_precision')
        ra = m.get('refusal_accuracy', '<缺键>')
        print(f"[{ts}] v={ver} src={src!r} model={m.get('model')} neg_f={nf} neg_c={nc} refusal_acc={ra}")
print()

print("=" * 70)
print("三、演示记录中的 refusal_accuracy 值（供对照）")
print("=" * 70)
for m in demom:
    print(f"model={m.get('model')} refusal_acc={m.get('refusal_accuracy', '<缺键>')}")