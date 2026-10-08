"""测试集生成/加载（与 main 中 generate_testset / load_custom_testset / load_benchmark 一致）。"""
import json
import os

import pandas as pd
from datasets import load_dataset

from backend.core.utils import safe_json_loads
from .negatives import generate_negative_testset


def generate_testset(chunks, llm, num=3):
    """自动从文档生成测试问答对，含动态负样本 + 编码容错 + 重试机制"""
    testset = []
    prompt_template = """请根据下面的文档片段，生成1个中文问题和对应的标准答案。
要求：
1. 问题完整，答案必须严格来自文档内容，不要额外发挥。
2. 答案要简短准确。
3. 严格按JSON格式输出，不要多余解释，不要加代码块标记：
{{"question": "你的问题", "ground_truth": "标准答案"}}

文档片段：
{chunk}
"""

    valid_chunks = [c for c in chunks if len(c.page_content) >= 50]
    if not valid_chunks:
        raise RuntimeError("有效文档片段不足，无法生成测试集")

    max_retry = 2
    for i, chunk in enumerate(valid_chunks[:num]):
        success = False
        for retry in range(max_retry + 1):
            try:
                # 强制UTF-8转码，过滤异常字符，从根源避免ASCII编码错误
                chunk_content = str(chunk.page_content).encode("utf-8", errors="ignore").decode("utf-8")
                prompt = prompt_template.format(chunk=chunk_content)

                resp = llm.invoke(prompt)
                content = str(resp.content).strip()
                qa = safe_json_loads(content)

                testset.append({
                    "question": qa["question"],
                    "ground_truth": qa["ground_truth"],
                    "contexts": [chunk_content],
                    "case_type": "positive"
                })
                success = True
                break
            except UnicodeEncodeError as e:
                print(f"⚠️ 第{i + 1}题编码异常，重试 {retry + 1}/{max_retry}: {str(e)[:50]}")
                continue
            except Exception as e:
                print(f"⚠️ 第{i + 1}题生成失败，重试 {retry + 1}/{max_retry}: {str(e)[:60]}")
                continue

        if not success:
            print(f"❌ 第{i + 1}题多次重试失败，跳过")

    if len([t for t in testset if t["case_type"] == "positive"]) == 0:
        raise RuntimeError("正向测试集全部生成失败，请检查LLM连接、密钥与编码配置")

    testset.extend(generate_negative_testset(chunks, llm, testset, num=3))
    return testset


def load_custom_testset(path):
    """从 JSON / CSV 加载自定义测试集。

    支持字段：
      - question     必填，问题文本
      - ground_truth 选填，标准答案
      - case_type    选填，positive / negative，默认 positive
      - contexts     选填，预定义上下文（仅 JSON 支持，暂不参与检索）
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"测试集文件不存在: {path}")

    ext = os.path.splitext(path)[1].lower()
    testset = []

    if ext == ".json":
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("testset", data.get("cases", []))
        if not isinstance(data, list):
            raise ValueError("JSON 测试集必须是列表，或含 testset/cases 字段的对象")
        for item in data:
            testset.append({
                "question": str(item.get("question", "")).strip(),
                "ground_truth": str(item.get("ground_truth", "")).strip(),
                "contexts": item.get("contexts", []),
                "case_type": str(item.get("case_type", "positive")).strip() or "positive",
            })
    elif ext in (".csv", ".txt"):
        df = pd.read_csv(path, encoding="utf-8-sig").fillna("")
        for _, row in df.iterrows():
            testset.append({
                "question": str(row.get("question", "")).strip(),
                "ground_truth": str(row.get("ground_truth", "")).strip(),
                "contexts": [],
                "case_type": str(row.get("case_type", "positive")).strip() or "positive",
            })
    else:
        raise ValueError(f"不支持的测试集格式: {ext}，仅支持 .json / .csv")

    testset = [t for t in testset if t["question"]]
    if not testset:
        raise RuntimeError("自定义测试集为空，请检查 question 字段")
    return testset


def load_benchmark(name, limit=20):
    """从 HuggingFace 基准数据集加载测试集，转成与 load_custom_testset 一致的结构。

    当前支持：
      - truthful_qa（generation 配置，取 validation 集）
        字段映射：question -> question，best_answer -> ground_truth
    首次加载需联网；失败时提示配置 HF_ENDPOINT。
    """
    BENCHMARKS = {
        "truthful_qa": {
            "path": "truthfulqa/truthful_qa",
            "config": "generation",
            "split": "validation",
            "question_field": "question",
            "ground_truth_field": "best_answer",
        },
    }

    if name not in BENCHMARKS:
        raise ValueError(f"不支持的基准数据集: {name}，可选: {', '.join(BENCHMARKS)}")

    cfg = BENCHMARKS[name]
    try:
        ds = load_dataset(cfg["path"], cfg["config"], split=cfg["split"])
    except Exception as e:
        raise RuntimeError(
            f"基准数据集 {name} 加载失败（首次加载需联网）。"
            f"请确认已设置 HF_ENDPOINT=https://hf-mirror.com 后重试。"
            f"原始错误: {str(e)[:200]}"
        ) from e

    print(f"✅ 基准数据集 {name} 字段: {list(ds.features.keys())}")
    if limit is not None:
        ds = ds.select(range(min(limit, len(ds))))

    testset = []
    for row in ds:
        q = str(row.get(cfg["question_field"], "")).strip()
        if not q:
            continue
        testset.append({
            "question": q,
            "ground_truth": str(row.get(cfg["ground_truth_field"], "")).strip(),
            "contexts": [],
            "case_type": "positive",
        })

    if not testset:
        raise RuntimeError(f"基准数据集 {name} 抽样为空，请检查字段映射")
    return testset
