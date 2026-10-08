"""基于本地 JSON 文件的历史记录存储。

load_history / save_eval_record / history_to_dataframe 与 main.py 原函数逐行一致，
仅路径常量改从 backend.config 读取。
"""
import json
import os

import pandas as pd

from backend import config
from .base import HistoryStore


def load_history(path=config.HISTORY_PATH):
    """加载评测历史记录，容错返回列表（不存在/为空/格式损坏/版本不兼容均自动返回安全值）"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def save_eval_record(record, path=config.HISTORY_PATH):
    """追加一条评测记录到历史文件"""
    history = load_history(path)
    history.append(record)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 历史记录保存失败: {str(e)[:80]}")


def history_to_dataframe(path=config.HISTORY_PATH):
    """把历史记录展平成 DataFrame（一模型一行），便于表格展示"""
    rows = []
    for run in load_history(path):
        models = run.get("models")
        if not isinstance(models, list):
            continue
        for m in models:
            if not isinstance(m, dict):
                continue
            rows.append({
                "版本": run.get("version", ""),
                "时间": run.get("timestamp", ""),
                "文档": run.get("doc_name", ""),
                "测试集": run.get("testset_source", ""),
                "模型": m.get("model", ""),
                "忠实度": m.get("faithfulness"),
                "上下文精度": m.get("context_precision"),
                "答案相关性": m.get("answer_relevancy"),
                "事实一致性": m.get("answer_correctness"),
                "回答完整性": m.get("context_recall"),
                "拒答准确率": m.get("refusal_accuracy"),
                "幻觉率": m.get("hallucination_rate"),
                "综合得分": m.get("score"),
                "门禁": "通过" if run.get("gate_passed") else "拦截",
            })
    columns = ["版本", "时间", "文档", "测试集", "模型", "忠实度", "上下文精度", "答案相关性", "事实一致性", "回答完整性", "拒答准确率", "幻觉率", "综合得分", "门禁"]
    return pd.DataFrame(rows, columns=columns)


class JSONHistoryStore(HistoryStore):
    """JSON 文件历史存储实现（委托上述模块级函数）。"""

    def load(self, path=None):
        return load_history(path if path is not None else config.HISTORY_PATH)

    def save(self, record, path=None):
        save_eval_record(record, path if path is not None else config.HISTORY_PATH)

    def to_dataframe(self, path=None):
        return history_to_dataframe(path if path is not None else config.HISTORY_PATH)
