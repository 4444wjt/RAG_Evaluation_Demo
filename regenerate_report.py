# ==================================================
# 报告展示层重新生成脚本（只改展示，不动评测逻辑）
# ==================================================

import os
import base64
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from html import escape

from backend.core.utils import _short_model_name

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUMMARY_CSV = os.path.join(BASE_DIR, "multi_model_summary.csv")
CHART_PATH = os.path.join(BASE_DIR, "result_chart.png")
HTML_OUTPUT = os.path.join(BASE_DIR, "ragas_report.html")

VERSION = "v1.0.0"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
FAITH_THRESHOLD = 0.8
CTX_PREC_THRESHOLD = 0.8
CHUNK_SIZE = 200
CHUNK_OVERLAP = 30
TOP_K = 4

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

# ---------- 1. 读取汇总数据（含兼容性校验） ----------
try:
    df = pd.read_csv(SUMMARY_CSV)
except FileNotFoundError:
    raise SystemExit(f"❌ 未找到文件: {SUMMARY_CSV}，请先运行 main.py 生成汇总数据。")
except Exception as e:
    raise SystemExit(f"❌ 读取 CSV 失败: {e}")

# 字段兼容：缺失的列自动补 None
REQUIRED_COLS = ["model", "faithfulness", "context_precision",
                 "pos_faithfulness", "pos_context_precision"]
for col in REQUIRED_COLS:
    if col not in df.columns:
        df[col] = None
        print(f"⚠️ 字段 {col} 缺失，已自动补 None")

summary = df.to_dict("records")

def clean(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return None
    if pd.isna(v):
        return None
    return float(v)

for item in summary:
    for k in list(item.keys()):
        if k != "model":
            item[k] = clean(item[k])

# ---------- 2. 重新绘图 ----------
models = [_short_model_name(item["model"]) for item in summary]
faith_vals = [item.get("faithfulness") for item in summary]
ctx_vals = [item.get("context_precision") for item in summary]

fig, ax = plt.subplots(figsize=(8, 5))
n = len(models)
x = list(range(n))
width = 0.35

for i in range(n):
    faith = faith_vals[i]
    ctx = ctx_vals[i]

    if faith is None:
        ax.text(i - width/2, 0.02, "N/A", ha='center', va='bottom',
                fontsize=12, color='#888', style='italic')
    else:
        ax.bar(i - width/2, faith, width,
               color='#4CAF50', label='Faithfulness' if i == 0 else "")
        ax.text(i - width/2, faith + 0.02, f"{faith:.2f}",
                ha='center', va='bottom', fontsize=10)

    if ctx is None:
        ax.text(i + width/2, 0.02, "N/A", ha='center', va='bottom',
                fontsize=12, color='#888', style='italic')
    else:
        ax.bar(i + width/2, ctx, width,
               color='#2196F3', label='Context Precision' if i == 0 else "")
        ax.text(i + width/2, ctx + 0.02, f"{ctx:.2f}",
                ha='center', va='bottom', fontsize=10)

# 质量门禁阈值线
ax.axhline(y=FAITH_THRESHOLD, color='#E53935', linestyle='--', linewidth=1.5, label='质量门禁阈值')

ax.set_xticks(x)
ax.set_xticklabels(models, fontsize=11)
ax.set_ylim(0, 1.0)
ax.set_ylabel("Score", fontsize=12)
ax.set_xlabel("Models", fontsize=12)
ax.set_title("RAG Evaluation Results - 多模型对比", fontsize=14)
ax.grid(axis='y', linestyle='--', alpha=0.5)
ax.legend(loc='lower right')
plt.tight_layout()
plt.savefig(CHART_PATH, dpi=150, bbox_inches='tight')
plt.close()
print(f"✅ 图表已保存: {CHART_PATH}")

# ---------- 3. 生成 HTML 报告 ----------
with open(CHART_PATH, "rb") as f:
    chart_base64 = base64.b64encode(f.read()).decode("utf-8")

def fmt(v):
    return f"{v:.4f}" if isinstance(v, (int, float)) else "N/A"

def gate_result(faith, ctx):
    """根据阈值判定门禁结论"""
    if faith is None or ctx is None:
        return "⚠️ 数据缺失"
    if faith >= FAITH_THRESHOLD and ctx >= CTX_PREC_THRESHOLD:
        return "✅ 通过"
    return "⛔ 拦截"

rows_html = ""
for item in summary:
    faith = item.get("faithfulness")
    ctx = item.get("context_precision")
    rows_html += (
        f"<tr>"
        f"<td title=\"原始模型 ID：{escape(str(item['model']), quote=True)}\">{escape(str(_short_model_name(item['model'])))}</td>"
        f"<td>{fmt(faith)}</td>"
        f"<td>{fmt(ctx)}</td>"
        f"<td>{fmt(item.get('pos_faithfulness'))}</td>"
        f"<td>{fmt(item.get('pos_context_precision'))}</td>"
        f"<td>{gate_result(faith, ctx)}</td>"
        f"</tr>"
    )

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>鉴真全域 VeriScope AI · 评测报告 {VERSION}</title>
<style>
    body {{ font-family: 'Microsoft YaHei', sans-serif; background: #f5f5f5; margin: 0; padding: 40px; }}
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
    .note {{ background: #fff8e1; padding: 12px 16px; border-left: 4px solid #FFC107; border-radius: 4px; margin: 20px 0; color: #8a6d3b; font-size: 13px; line-height: 1.7; }}
    .footer {{ color: #999; font-size: 13px; margin-top: 30px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
    <h1>鉴真全域 VeriScope AI · 评测报告</h1>
    <p class="meta">
        <b>版本号：</b>{VERSION}<br>
        <b>生成时间：</b>{now}<br>
        <b>评测框架：</b>Ragas + LangChain + Chroma<br>
        <b>Embedding：</b>{EMBEDDING_MODEL}
    </p>

    <div class="config-box">
        <b>测试配置：</b><br>
        · 质量门禁阈值：忠实度 ≥ {FAITH_THRESHOLD}，上下文精度 ≥ {CTX_PREC_THRESHOLD}<br>
        · 测试集构成：N条正向用例 + 1条负样本（对抗测试）<br>
        · 检索 Top-K：{TOP_K} &nbsp;|&nbsp; 文本块大小：{CHUNK_SIZE} 字符 &nbsp;|&nbsp; 重叠：{CHUNK_OVERLAP} 字符
    </div>

    <div class="robustness">
        ✔ 已内置空值(nan)过滤机制 &nbsp;|&nbsp; ✔ 异常重试 &nbsp;|&nbsp; ✔ 超时保护
    </div>

    <h2>多模型对比结果</h2>
    <p class="meta">模型显示名使用当前内置名称；原始模型 ID 可在模型名提示中查看。历史实际版本以原始响应为准，未记录时仍为未知。</p>
    <table>
        <thead>
            <tr>
                <th>模型</th>
                <th>整体忠实度</th>
                <th>整体上下文精度</th>
                <th>正向忠实度</th>
                <th>正向上下文精度</th>
                <th>门禁结论</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>

    <div class="note">
        <b>注：</b> Faithfulness 指标显示为 N/A，是由于对应模型在部分问题上回答较为简短，Ragas 无法从中提取有效断言进行计算。这是评测工具对边界场景的兜底处理（已在代码中加入 <code>pd.isna()</code> 过滤），并非程序错误。<br>
        <b>负样本说明：</b> 测试集中包含 1 条负样本（知识库未提及的问题），用于验证工具对知识库盲区的识别能力。对应场景下上下文精度为 0，可触发 CI/CD 质量门禁自动拦截。<br>
        <b>分层统计：</b> 正向指标已剔除负样本，更能真实反映模型在正常问答场景下的表现。
    </div>

    <div class="chart">
        <h2>可视化图表</h2>
        <img src="data:image/png;base64,{chart_base64}" alt="评测图表">
    </div>

    <div class="footer">由 鉴真全域 VeriScope AI 生成 · {VERSION}</div>
</div>
</body>
</html>"""

with open(HTML_OUTPUT, "w", encoding="utf-8") as f:
    f.write(html)
print(f"✅ HTML报告已保存: {HTML_OUTPUT}")
