"""真实评测绘图（逐行迁移 main.py L1394~1481）。

与演示图 draw_summary_chart 视觉风格一致（figsize 12x7.44、facecolor #141f28、
subplots_adjust、margins、图例移出），但含真实评测独有语义：门禁阈值线 axhline、
单模型分支的图例、ylabel fontsize=14、不设置 spines 颜色。独立实现，不调用 draw_summary_chart。
"""
import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from backend.core.utils import _short_model_name, _resolve_cjk_font, CJK_FONT_CANDIDATES

# 6 项评测指标元数据（与 main.METRICS / main.METRIC_KEYS 一致，供真实评测柱状图使用）
METRICS = [
    {"key": "faithfulness", "label": "忠实度", "en": "Faithfulness"},
    {"key": "context_precision", "label": "上下文精度", "en": "Context Precision"},
    {"key": "answer_relevancy", "label": "答案相关性", "en": "Answer Relevancy"},
    {"key": "answer_correctness", "label": "事实一致性", "en": "Answer Correctness"},
    {"key": "context_recall", "label": "回答完整性", "en": "Context Recall"},
]
METRIC_KEYS = [m["key"] for m in METRICS]


def generate_evaluation_chart(summary, faith_threshold, chart_path):
    """生成真实评测能力分析柱状图，返回实际 chart_path。

    参数 summary 为完整 summary 列表（含失败行），内部按 faithfulness 非 None 过滤，
    空则抛 RuntimeError("所有模型评测全部失败")，与 main.py 行为一致。
    """
    valid_summary = [s for s in summary if s["faithfulness"] is not None]
    if not valid_summary:
        raise RuntimeError("所有模型评测全部失败")

    # 设置中文字体并检测系统是否真实存在 CJK 字体，缺失时图表标签自动降级为英文
    cjk_font = _resolve_cjk_font()
    use_cjk = cjk_font is not None
    if use_cjk:
        plt.rcParams['font.sans-serif'] = [cjk_font] + CJK_FONT_CANDIDATES + ['DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    # 宽高比/主题/图例样式与 draw_summary_chart、趋势图保持一致（10:6.2 = 1.613 → 12x7.44）
    fig, ax = plt.subplots(figsize=(12, 7.44), facecolor="#141f28")
    ax.set_facecolor("#141f28")
    labels = [m["label"] if use_cjk else m["en"] for m in METRICS]
    keys = METRIC_KEYS
    x = np.arange(len(keys))
    width = 0.12
    colors = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#E53935']

    def _scores_of(item):
        out = []
        for k in keys:
            v = item.get(k)
            out.append(0.0 if v is None or pd.isna(v) else float(v))
        return out

    if len(valid_summary) > 1:
        n = len(valid_summary)
        for i, item in enumerate(valid_summary):
            scores = _scores_of(item)
            offset = (i - n / 2 + 0.5) * width
            bars = ax.bar(x + offset, scores, width, label=_short_model_name(item['model']), color=colors[i % len(colors)])
            for bar, score in zip(bars, scores):
                ax.annotate(f'{score:.2f}', xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                            xytext=(0, 5), textcoords="offset points", ha='center', va='bottom', fontsize=12, color="#efece6")
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.10), ncol=len(valid_summary), fontsize=10.5, borderpad=0.45, handlelength=1.2, handletextpad=0.6, columnspacing=1.2,
                  facecolor="#141f28", edgecolor="#34313f", labelcolor="#efece6", framealpha=0.9)
    else:
        scores = _scores_of(valid_summary[0])
        bars = ax.bar(x, scores, color=colors, width=0.5)
        for bar, score in zip(bars, scores):
            ax.annotate(f'{score:.2f}', xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 5), textcoords="offset points", ha='center', va='bottom', fontsize=13, color="#efece6")

    ax.set_ylim(0, 1.08)
    ax.set_title('模型评测能力分析' if use_cjk else 'Model Capability Analysis', fontsize=20, pad=10, color="#efece6")
    ax.set_ylabel('Score', fontsize=14, color="#efece6", labelpad=6)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#efece6")
    ax.tick_params(colors="#c0bacd", labelsize=13)
    ax.grid(axis='y', linestyle='--', alpha=0.35, color="#34313f")
    ax.axhline(y=faith_threshold, color='#E53935', linestyle='--', linewidth=1.5,
               label=f'门禁阈值({faith_threshold})' if use_cjk else f'Gate Threshold ({faith_threshold})')

    if len(valid_summary) == 1:
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.10), fontsize=10.5, facecolor="#141f28", edgecolor="#34313f", labelcolor="#efece6", framealpha=0.9)

    # 绘图区最大化：与 draw_summary_chart 相同的边距策略，图例移出、收紧左右留白
    ax.margins(x=0.01)
    fig.subplots_adjust(left=0.055, right=0.99, top=0.93, bottom=0.15)
    if os.path.exists(chart_path):
        os.remove(chart_path)
    # 不用 bbox_inches='tight'：固定输出 1800x1116（比例 1.613），与趋势图/演示图完全一致
    plt.savefig(chart_path, dpi=150, facecolor="#141f28")
    plt.close()
    print(f"✅ 图表已保存: {chart_path}")
    return chart_path
