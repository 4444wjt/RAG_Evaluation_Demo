import json

HP = r'D:\RAG_Evaluation_Demo\eval_history.json'
data = json.load(open(HP, encoding='utf-8-sig'))
print("总记录数:", len(data))

new_run = None
for r in reversed(data):
    if r.get('testset_source') == '自动生成':
        new_run = r
        break

print("最新自动生成记录: version=", new_run['version'], "| timestamp=", new_run['timestamp'])
for m in new_run.get('models', []) or []:
    print(f"  model={m.get('model')}  refusal_accuracy={m.get('refusal_accuracy', '<缺键>')}  neg_f={m.get('neg_faithfulness')}  neg_c={m.get('neg_context_precision')}")

# 统计所有非演示模型记录中 refusal_accuracy 非空的条数
demo_src = "内置演示数据"
real = [m for r in data if r.get('testset_source') != demo_src for m in (r.get('models') or []) if isinstance(m, dict)]
non_null = [m for m in real if m.get('refusal_accuracy') is not None]
print(f"真实记录中 refusal_accuracy 非 None 的模型记录数: {len(non_null)} / {len(real)}")