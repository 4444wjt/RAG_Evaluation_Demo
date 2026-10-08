"""本地文件工件读写原语（评测历史 JSON 文件的底层容错读写）。

与 main.py 中 load_history / save_eval_record 内部的读写逻辑一致，供各存储实现复用。
"""
import json
import os


def read_json_list(path):
    """容错读取 JSON 文件为列表（不存在/为空/损坏/结构不符均返回空列表）。"""
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


def write_json(path, data):
    """把数据写入 JSON 文件；失败打印告警（不抛出）。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 历史记录保存失败: {str(e)[:80]}")
