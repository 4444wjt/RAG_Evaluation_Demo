# ==================================================
# RAG幻觉自动化评测工具
# 功能：文档加载 → 向量化 → 自动生成测试集 → RAG问答 → Ragas评测 → 多模型对比 → 分层质量门禁 → HTML报告
# ==================================================

# ========== 0. 基础环境修复（必须放在最前） ==========
import socket

# 强制禁用 IPv6，只使用 IPv4
_original_getaddrinfo = socket.getaddrinfo

def _force_ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = _force_ipv4_getaddrinfo
import sys
import os

# ===== Windows编码终极兜底 =====
if sys.platform == "win32":
    import codecs

    # 注册编码错误忽略处理器，从底层拦截ASCII报错
    codecs.register_error("strict", codecs.ignore_errors)

import asyncio
import platform

# Windows 异步事件循环修复
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 强制标准输入输出全链路UTF-8编码，忽略异常字符
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
    sys.stdin.reconfigure(encoding='utf-8', errors='ignore')

# 强制 IPv4 网络（解决IPv6链路不通导致的超时/连接失败）
# if os.environ.get("FORCE_IPV4", "true").lower() == "true":
#     import socket
#
#     _original_getaddrinfo = socket.getaddrinfo
#
#
#     def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
#         return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
#
#
#     socket.getaddrinfo = _patched_getaddrinfo

# HuggingFace 国内镜像加速
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"

# 屏蔽非关键弃用警告
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ========== 1. 依赖导入 ==========
import base64
import json
import re
import shutil
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import httpx

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

from ragas import evaluate, RunConfig
from ragas.metrics import faithfulness, context_precision
from datasets import Dataset

# ========== 2. 全局常量配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 核心路径配置
DOC_PATH = os.path.join(BASE_DIR, "demo.txt")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
SUMMARY_OUTPUT = os.path.join(BASE_DIR, "multi_model_summary.csv")
HTML_OUTPUT = os.path.join(BASE_DIR, "ragas_report.html")
CHART_PATH = os.path.join(BASE_DIR, "result_chart.png")

# 模型配置
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
REUSE_VECTOR_DB = False
TEST_QA_NUM = 3
VERSION = "v1.2.0"

# 文本切分参数
CHUNK_SIZE = 200
CHUNK_OVERLAP = 30
TOP_K = 4

# 质量门禁阈值
FAITH_THRESHOLD = 0.8
CTX_PREC_THRESHOLD = 0.8
SINGLE_CASE_WARN_THRESHOLD = 0.5

# 超时配置
LLM_TIMEOUT = 180
LLM_MAX_RETRIES = 2
EVAL_TIMEOUT = 300

# ====================== 多模型配置（安全版：环境变量读取密钥） ======================
MODELS_CONFIG = [
    {
        "name": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "OPENAI_API_KEY"
    },
    {
        "name": "Pro/Qwen/Qwen2.5-7B-Instruct",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY"
    },
]

# 备注：如果通义千问调用报错，可尝试将模型名替换为 qwen3.8-flash-instruct
# =================================================================================

# ========== 3. 工具函数 ==========
def safe_json_loads(text):
    """增强版JSON解析，多层降级容错，适配大模型输出格式不稳定的场景"""
    text = text.strip()
    # 1. 直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2. 提取代码块
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except Exception:
            pass
    # 3. 正则提取最外层大括号
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    raise ValueError(f"无法解析JSON格式: {text[:100]}...")


def get_llm(model_cfg):
    """根据配置初始化对应的LLM，优化超时、重试与编码兼容性"""
    api_key = os.environ.get(model_cfg["api_key_env"])
    if not api_key:
        raise ValueError(
            f"API密钥未配置，请设置环境变量: {model_cfg['api_key_env']}\n"
            f"当前模型: {model_cfg['name']}"
        )

    # 密钥ASCII预校验，提前发现编码隐患
    if not api_key.isascii():
        print(f"⚠️ 警告：{model_cfg['name']}的API密钥包含非ASCII字符，可能导致调用失败")

    # 显式创建HTTP客户端，底层链路可控，减少原生编码问题
    http_client = httpx.Client(
        timeout=LLM_TIMEOUT,
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5
        )
    )
    return ChatOpenAI(
        model=model_cfg["name"],
        base_url=model_cfg["base_url"],
        api_key=api_key,
        temperature=0,
        max_retries=LLM_MAX_RETRIES,
        timeout=LLM_TIMEOUT,
        http_client=http_client
    )


def load_and_split_doc(path):
    """加载并切分文档"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文档不存在: {path}")

    loader = TextLoader(path, encoding="utf-8-sig")
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", " ", ""]
    )
    chunks = loader.load_and_split(text_splitter)

    if not chunks:
        raise RuntimeError("文档切分后为空，请检查文档内容长度")
    return chunks


def build_vector_store(chunks, embeddings):
    """构建向量库，支持复用已有库"""
    if REUSE_VECTOR_DB and os.path.exists(CHROMA_DIR):
        print("ℹ️ 复用已有向量库")
        return Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=embeddings
        )

    # 强制重建
    shutil.rmtree(CHROMA_DIR, ignore_errors=True)
    return Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )


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

    # 生成动态负样本
    negative_questions = [
        ("这个项目的创始人出生日期是哪天？", "文档未提及"),
        ("火星的直径是多少公里？", "文档未提及"),
        ("世界上最深的海沟叫什么名字？", "文档未提及"),
    ]
    for q, a in negative_questions:
        testset.append({
            "question": q,
            "ground_truth": a,
            "contexts": [],
            "case_type": "negative"
        })

    if len([t for t in testset if t["case_type"] == "positive"]) == 0:
        raise RuntimeError("正向测试集全部生成失败，请检查LLM连接、密钥与编码配置")

    return testset


def rag_answer(question, retriever, llm):
    """RAG检索问答，强制输出完整句子"""
    docs = retriever.invoke(question)
    contexts = [d.page_content for d in docs]

    prompt = f"""
    请根据以下参考资料，用一段完整的、详细的中文句子回答问题。
    如果参考资料中没有相关信息，请直接回答“根据提供的文档，无法回答该问题。”，不要编造。

    参考资料：
    {chr(10).join(contexts)}

    问题：{question}
    答案：
    """
    answer = llm.invoke(prompt).content.strip()
    return answer, contexts


def generate_html_report(summary_data, chart_path=CHART_PATH, output_path=HTML_OUTPUT):
    """生成带配置说明的HTML报告"""
    with open(chart_path, "rb") as f:
        chart_base64 = base64.b64encode(f.read()).decode("utf-8")

    def fmt(v):
        return f"{v:.4f}" if isinstance(v, (int, float)) and pd.notna(v) else "N/A"

    rows_html = ""
    for item in summary_data:
        rows_html += (
            f"<tr>"
            f"<td>{item['model']}</td>"
            f"<td>{fmt(item.get('faithfulness'))}</td>"
            f"<td>{fmt(item.get('context_precision'))}</td>"
            f"<td>{fmt(item.get('pos_faithfulness'))}</td>"
            f"<td>{fmt(item.get('pos_context_precision'))}</td>"
            f"</tr>"
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>RAG 评测报告 {VERSION}</title>
<style>
    body {{ font-family: 'Microsoft YaHei', 'WenQuanYi Micro Hei', sans-serif; background: #f5f5f5; margin: 0; padding: 40px; }}
    .container {{ max-width: 1000px; margin: 0 auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }}
    h1 {{ color: #2c3e50; border-bottom: 3px solid #4CAF50; padding-bottom: 12px; }}
    h2 {{ color: #34495e; margin-top: 30px; }}
    .meta {{ color: #888; font-size: 14px; line-height: 1.8; }}
    .config-box {{ background: #f8f9fa; padding: 16px; border-radius: 8px; margin: 20px 0; font-size: 14px; color: #555; }}
    table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
    th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #eee; }}
    th {{ background: #f8f9fa; color: #555; font-weight: 600; }}
    tr:hover {{ background: #fafafa; }}
    .chart {{ text-align: center; margin: 30px 0; }}
    .chart img {{ max-width: 100%; border-radius: 8px; }}
    .robustness {{ background: #e8f5e9; padding: 12px 16px; border-left: 4px solid #4CAF50; border-radius: 4px; margin: 20px 0; color: #2e7d32; font-size: 14px; }}
    .footer {{ color: #999; font-size: 13px; margin-top: 30px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
    <h1>RAG 幻觉自动化评测报告</h1>
    <p class="meta">
        <b>版本号：</b>{VERSION}<br>
        <b>生成时间：</b>{now}<br>
        <b>评测框架：</b>Ragas + LangChain + Chroma<br>
        <b>Embedding：</b>{EMBEDDING_MODEL}
    </p>

    <div class="config-box">
        <b>测试配置：</b><br>
        · 质量门禁阈值：忠实度 ≥ {FAITH_THRESHOLD}，上下文精度 ≥ {CTX_PREC_THRESHOLD}<br>
        · 测试集构成：{TEST_QA_NUM}条正向用例 + 3条负样本（对抗测试）<br>
        · 检索 Top-K：{TOP_K} &nbsp;|&nbsp; 文本块大小：{CHUNK_SIZE} 字符 &nbsp;|&nbsp; 重叠：{CHUNK_OVERLAP} 字符
    </div>

    <div class="robustness">
        ✔ 已内置空值(nan)过滤机制 &nbsp;|&nbsp; ✔ 异常重试 &nbsp;|&nbsp; ✔ 超时保护 &nbsp;|&nbsp; ✔ JSON格式容错
    </div>

    <h2>多模型对比结果</h2>
    <table>
        <thead>
            <tr>
                <th>模型</th>
                <th>整体忠实度</th>
                <th>整体上下文精度</th>
                <th>正向忠实度</th>
                <th>正向上下文精度</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    <p style="color:#888; font-size:13px;">
        * 正向指标剔除了负样本，避免负样本拉低均值导致误判。
    </p>

    <div class="chart">
        <h2>可视化图表</h2>
        <img src="data:image/png;base64,{chart_base64}" alt="评测图表">
    </div>

    <div class="footer">由 RAG 幻觉自动化评测工具 {VERSION} 生成</div>
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML报告已保存: {output_path}")


# ========== 4. 主流程 ==========
if __name__ == "__main__":
    print("=" * 50)
    print(f"🚀 RAG幻觉自动化评测工具 {VERSION} 启动")
    print("=" * 50)
    print("[健壮性] 已内置空值过滤、异常重试、超时保护、JSON格式容错")

    # 1. 初始化嵌入模型
    print("\n[1/6] 初始化嵌入模型...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"local_files_only": True}  # 强制只读本地缓存，不联网
    )

    # 2. 加载切分文档
    print("\n[2/6] 加载并切分文档...")
    chunks = load_and_split_doc(DOC_PATH)
    print(f"✅ 文档切分完成，共 {len(chunks)} 个块（chunk_size={CHUNK_SIZE}）")

    # 3. 构建向量库
    print("\n[3/6] 构建向量库...")
    vector_store = build_vector_store(chunks, embeddings)
    retriever = vector_store.as_retriever(search_kwargs={"k": TOP_K})
    print(f"✅ 向量库就绪（Top-K={TOP_K}）")

    # 4. 生成统一测试集（优先通义千问，中文编码兼容性更好；失败自动回退DeepSeek）
    print("\n[4/6] 自动生成测试集...")
    try:
        llm_for_testset = get_llm(MODELS_CONFIG[1])
        testset = generate_testset(chunks, llm_for_testset, TEST_QA_NUM)
    except Exception as e:
        print(f"⚠️ 通义千问生成测试集失败，切换DeepSeek: {str(e)[:60]}")
        llm_for_testset = get_llm(MODELS_CONFIG[0])
        testset = generate_testset(chunks, llm_for_testset, TEST_QA_NUM)
    print(f"✅ 生成 {len(testset)} 道测试题（含3道负样本）")

    # 5. 循环评测每个模型
    print("\n[5/6] 开始多模型横向评测...")
    summary = []
    model_dfs = {}

    for model_cfg in MODELS_CONFIG:
        print(f"\n{'=' * 50}")
        print(f"🎯 开始评测模型: {model_cfg['name']}")
        print('=' * 50)

        try:
            llm = get_llm(model_cfg)

            # 执行 RAG 问答
            eval_data = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
            for idx, item in enumerate(testset):
                answer, contexts = rag_answer(item["question"], retriever, llm)
                eval_data["question"].append(item["question"])
                eval_data["answer"].append(answer)
                eval_data["contexts"].append(contexts)
                eval_data["ground_truth"].append(item["ground_truth"])
                if (idx + 1) % 2 == 0:
                    print(f"  进度: {idx + 1}/{len(testset)}")

            # Ragas 评测
            dataset = Dataset.from_dict(eval_data)
            result = evaluate(
                dataset=dataset,
                metrics=[faithfulness, context_precision],
                llm=llm,
                embeddings=embeddings,
                run_config=RunConfig(max_workers=1, timeout=EVAL_TIMEOUT)
            )
            df = result.to_pandas()

            df["case_type"] = [item.get("case_type", "positive") for item in testset]
            model_dfs[model_cfg["name"]] = df

            safe_name = model_cfg["name"].replace("/", "_")  # 把斜杠替换成下划线
            csv_path = os.path.join(BASE_DIR, f"ragas_report_{safe_name}.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")

            # 分层统计
            pos_df = df[df["case_type"] == "positive"]
            neg_df = df[df["case_type"] == "negative"]

            mean_faith = df["faithfulness"].dropna().mean()
            mean_ctx = df["context_precision"].dropna().mean()
            pos_faith = pos_df["faithfulness"].dropna().mean() if len(pos_df) else None
            pos_ctx = pos_df["context_precision"].dropna().mean() if len(pos_df) else None
            neg_faith = neg_df["faithfulness"].dropna().mean() if len(neg_df) else None
            neg_ctx = neg_df["context_precision"].dropna().mean() if len(neg_df) else None

            summary.append({
                "model": model_cfg["name"],
                "faithfulness": round(mean_faith, 4) if pd.notna(mean_faith) else None,
                "context_precision": round(mean_ctx, 4) if pd.notna(mean_ctx) else None,
                "pos_faithfulness": round(pos_faith, 4) if pos_faith is not None and pd.notna(pos_faith) else None,
                "pos_context_precision": round(pos_ctx, 4) if pos_ctx is not None and pd.notna(pos_ctx) else None,
                "neg_faithfulness": round(neg_faith, 4) if neg_faith is not None and pd.notna(neg_faith) else None,
                "neg_context_precision": round(neg_ctx, 4) if neg_ctx is not None and pd.notna(neg_ctx) else None,
            })

            print(f"✅ {model_cfg['name']} 评测完成 | 整体忠实度: {mean_faith:.4f} | 整体上下文精度: {mean_ctx:.4f}")
            if pos_faith is not None and pd.notna(pos_faith):
                print(f"   正向用例 | 忠实度: {pos_faith:.4f} | 上下文精度: {pos_ctx:.4f}")

        except Exception as e:
            print(f"❌ 模型 {model_cfg['name']} 评测失败: {str(e)[:100]}")
            summary.append({
                "model": model_cfg["name"],
                "faithfulness": None,
                "context_precision": None,
                "pos_faithfulness": None,
                "pos_context_precision": None,
                "neg_faithfulness": None,
                "neg_context_precision": None,
            })

    # 全部失败边界处理
    valid_summary = [s for s in summary if s["faithfulness"] is not None]
    if not valid_summary:
        print("\n❌ 所有模型评测全部失败，终止后续流程")
        sys.exit(1)

    # 6. 多模型对比汇总
    print("\n" + "=" * 50)
    print("📊 多模型横向对比总结")
    print("=" * 50)
    summary_df = pd.DataFrame(summary)
    print(summary_df.to_string(index=False))
    summary_df.to_csv(SUMMARY_OUTPUT, index=False, encoding="utf-8-sig")
    print(f"\n✅ 汇总报告已保存: {SUMMARY_OUTPUT}")

    # 7. 生成可视化图表
    print("\n[6/6] 生成可视化图表...")

    # 分平台设置中文字体
    if platform.system() == "Linux":
        plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'WenQuanYi Micro Hei']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(10, 6))
    labels = ['Faithfulness', 'Context Precision']
    x = np.arange(len(labels))
    width = 0.35
    colors = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0']

    if len(valid_summary) > 1:
        for i, item in enumerate(valid_summary):
            model_name = item['model']
            faith = item.get('faithfulness')
            ctx = item.get('context_precision')
            faith = 0.0 if faith is None or pd.isna(faith) else float(faith)
            ctx = 0.0 if ctx is None or pd.isna(ctx) else float(ctx)
            scores = [faith, ctx]

            offset = (i - len(valid_summary) / 2 + 0.5) * width
            bars = ax.bar(x + offset, scores, width, label=model_name, color=colors[i % len(colors)])
            for bar, score in zip(bars, scores):
                height = bar.get_height()
                ax.annotate(f'{score:.2f}', xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=10)
        ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1), borderaxespad=0)
    else:
        mean_faith = valid_summary[0].get('faithfulness')
        mean_ctx = valid_summary[0].get('context_precision')
        mean_faith = 0.0 if mean_faith is None or pd.isna(mean_faith) else float(mean_faith)
        mean_ctx = 0.0 if mean_ctx is None or pd.isna(mean_ctx) else float(mean_ctx)
        scores = [mean_faith, mean_ctx]

        bars = ax.bar(labels, scores, color=['#4CAF50', '#2196F3'], width=0.5)
        for bar, score in zip(bars, scores):
            height = bar.get_height()
            ax.annotate(f'{score:.2f}', xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=12)

    ax.set_ylim(0, 1.0)
    ax.set_title('RAG 评测结果', fontsize=14, pad=15)
    ax.set_ylabel('Score', fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.grid(axis='y', linestyle='--', alpha=0.5)
    ax.axhline(y=0.8, color='#E53935', linestyle='--', linewidth=1.5, label='质量门禁阈值(0.8)')

    if len(valid_summary) == 1:
        ax.legend(loc='lower right')

    plt.tight_layout()
    if os.path.exists(CHART_PATH):
        os.remove(CHART_PATH)
    plt.savefig(CHART_PATH, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"✅ 图表已保存: {CHART_PATH}")

    # 8. 生成 HTML 报告
    generate_html_report(summary)

    # ========== 质量门禁 ==========
    print("\n" + "=" * 50)
    print("🔒 CI/CD 质量门禁校验")
    print("=" * 50)

    first_model = valid_summary[0]
    faith_score = first_model["faithfulness"]
    ctx_score = first_model["context_precision"]
    pos_faith = first_model.get("pos_faithfulness")
    pos_ctx = first_model.get("pos_context_precision")

    print(f"\n主评测模型: {first_model['model']}")
    print(f"--- 整体均值 ---")
    print(f"  平均忠实度 faithfulness:       {faith_score}")
    print(f"  平均上下文精度 context_precision: {ctx_score}")
    print(f"--- 正向用例均值（不含负样本）---")
    print(f"  正向忠实度 faithfulness:       {pos_faith}")
    print(f"  正向上下文精度 context_precision: {pos_ctx}")

    # 单用例异常告警
    print("\n[单用例异常告警]")
    warning_flag = False
    if first_model["model"] in model_dfs:
        for idx, row in model_dfs[first_model["model"]].iterrows():
            faith_val = row.get("faithfulness")
            if faith_val is not None and not pd.isna(faith_val) and faith_val < SINGLE_CASE_WARN_THRESHOLD:
                q_preview = str(row.get("question", ""))[:20]
                print(f"  ⚠️ 用例[{idx}] 忠实度低于{SINGLE_CASE_WARN_THRESHOLD}: {q_preview}...")
                warning_flag = True
    if not warning_flag:
        print("  ✅ 无单用例异常")

    # 整体均值阻断
    print("\n[整体均值阻断]")
    pass_flag = True
    if faith_score is None or pd.isna(faith_score) or faith_score < FAITH_THRESHOLD:
        print(f"  ❌ 不通过：整体忠实度低于阈值 {FAITH_THRESHOLD}")
        pass_flag = False
    if ctx_score is None or pd.isna(ctx_score) or ctx_score < CTX_PREC_THRESHOLD:
        print(f"  ❌ 不通过：整体上下文精度低于阈值 {CTX_PREC_THRESHOLD}")
        pass_flag = False

    if pass_flag:
        print("\n✅ 质量门禁通过，允许发布")
        sys.exit(0)
    else:
        print("\n⛔ 质量门禁拦截，终止发布")
        sys.exit(1)
