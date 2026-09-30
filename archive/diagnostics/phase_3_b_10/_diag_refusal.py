import json
from collections import Counter

data = json.load(open(r'D:\RAG_Evaluation_Demo\eval_history.json', encoding='utf-8-sig'))
allm = [m for r in data for m in (r.get('models') or []) if isinstance(m, dict)]
print('TOTAL_RUNS', len(data))
print('TOTAL_MODELS', len(allm))
print('WITH_REFUSAL_KEY', sum('refusal_accuracy' in m for m in allm))
print('WITHOUT_REFUSAL_KEY', sum('refusal_accuracy' not in m for m in allm))
print('WITH_NEG_SIGNAL', sum((m.get('neg_faithfulness') is not None) or (m.get('neg_context_precision') is not None) for m in allm))
print('KEYS_UNION', sorted(set().union(*[set(m.keys()) for m in allm])))
print('SOURCES', dict(Counter(r.get('testset_source') for r in data)))

# refusal_accuracy 值分布（对含该字段的记录）
ref_vals = [m.get('refusal_accuracy') for m in allm if 'refusal_accuracy' in m]
print('REFUSAL_VALUE_TYPES', Counter(type(v).__name__ for v in ref_vals))
print('REFUSAL_VALUES_SAMPLE', ref_vals[:20])

# 有 refusal_accuracy 字段的记录归属
print('--- runs/model WITH refusal key ---')
for i, r in enumerate(data):
    for m in (r.get('models') or []):
        if isinstance(m, dict) and 'refusal_accuracy' in m:
            print(f"  run {i} [{r.get('version')}] source={r.get('testset_source')} model={m.get('model')} refusal={m.get('refusal_accuracy')}")

# 每条 run 里，有负样本信号但无 refusal 字段的 model 名单
print('--- runs with neg signal but missing refusal key ---')
for i, r in enumerate(data):
    for m in (r.get('models') or []):
        if not isinstance(m, dict):
            continue
        neg_sig = (m.get('neg_faithfulness') is not None) or (m.get('neg_context_precision') is not None)
        has_ref = 'refusal_accuracy' in m
        if neg_sig and not has_ref:
            print(f"  run {i} [{r.get('version')}] model={m.get('model')} neg_faith={m.get('neg_faithfulness')} neg_ctx={m.get('neg_context_precision')} (NO refusal key)")