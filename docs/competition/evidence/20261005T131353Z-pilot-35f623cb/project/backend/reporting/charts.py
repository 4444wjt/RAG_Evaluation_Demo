"""图表生成（柱状图 / 趋势图 / 占位图）。

与 main.py 中 _write_no_data_trend / generate_trend_chart / draw_summary_chart /
placeholder_chart 逐行一致，仅常量与工具函数改为从 backend 层导入。
"""
import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from backend import config
from backend.core.utils import _short_model_name, _resolve_cjk_font, CJK_FONT_CANDIDATES
from backend.storage.json_history import load_history

# 6 项评测指标元数据（与 main.METRICS / main.METRIC_KEYS 一致，供柱状图使用）
METRICS = [
    {"key": "faithfulness", "label": "忠实度", "en": "Faithfulness"},
    {"key": "context_precision", "label": "上下文精度", "en": "Context Precision"},
    {"key": "answer_relevancy", "label": "答案相关性", "en": "Answer Relevancy"},
    {"key": "answer_correctness", "label": "事实一致性", "en": "Answer Correctness"},
    {"key": "context_recall", "label": "回答完整性", "en": "Context Recall"},
]
METRIC_KEYS = [m["key"] for m in METRICS]


def _write_no_data_trend(path, text):
    """输出一张与趋势图等尺寸（11×5.5 英寸）的占位图，避免 gr.Image 收到 None 后塌陷导致页面框架缩小"""
    cjk = _resolve_cjk_font()
    if cjk:
        plt.rcParams['font.sans-serif'] = [cjk] + CJK_FONT_CANDIDATES + ['DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
        text = "No trend data for this metric."
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(figsize=(10, 6.2), facecolor="#141f28")
    ax.set_facecolor("#141f28")
    ax.axis('off')
    ax.text(0.5, 0.5, text, ha='center', va='center', fontsize=18, color='#807c8c', transform=ax.transAxes)
    if os.path.exists(path):
        os.remove(path)
    fig.savefig(path, dpi=150, facecolor="#141f28")
    plt.close(fig)
    return path


def generate_trend_chart(model_names=None, metric="score", path=config.TREND_CHART_PATH):
    """从历史记录生成质量趋势折线图，返回图表路径；无数据时返回占位图"""
    history = load_history()
    if not history:
        return _write_no_data_trend(path, "暂无趋势数据，请先完成评测")

    # 排除内置演示数据，只统计真实评测（演示假数据会污染趋势与 X 轴标签）
    runs = [r for r in history if r.get("testset_source") != "内置演示数据"]
    if not runs:
        return _write_no_data_trend(path, "暂无真实评测数据")

    cjk_font = _resolve_cjk_font()
    if cjk_font:
        plt.rcParams['font.sans-serif'] = [cjk_font] + CJK_FONT_CANDIDATES + ['DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    all_models = []
    for run in runs:
        models = run.get("models")
        if not isinstance(models, list):
            continue
        for m in models:
            if isinstance(m, dict) and m.get("model") and m["model"] not in all_models:
                all_models.append(m["model"])
    targets = [x for x in (model_names or all_models) if x in all_models]

    metric_label = {
        "score": "综合得分",
        "hallucination_rate": "幻觉率",
        "faithfulness": "忠实度",
        "context_precision": "上下文精度",
        "answer_relevancy": "答案相关性",
        "answer_correctness": "事实一致性",
        "context_recall": "回答完整性",
        "refusal_accuracy": "拒答准确率",
    }.get(metric, metric)

    # 异构测试集：评测口径与常规不同（如 truthful_qa 低分基准），单独用 × 标记，避免误导
    HETERO_SOURCES = {"truthful_qa"}

    def _is_hetero(run):
        return run.get("testset_source") in HETERO_SOURCES

    # X 轴使用实验序号（Run 01…）：真实日期时间对评测趋势无分析价值；† 保留异构基准标记
    labels = [f"Run {k + 1:02d}" + ("†" if _is_hetero(run) else "") for k, run in enumerate(runs)]
    x_pos = list(range(len(runs)))

    fig, ax = plt.subplots(figsize=(10, 6.2), facecolor="#141f28")
    ax.set_facecolor("#141f28")
    colors = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#E53935']
    linestyles = ['-', '--', '-.', ':']
    markers = ['o', 's', 'D', '^', 'v']
    plotted = False
    hetero_plotted = False
    all_vals = []

    for i, target in enumerate(targets):
        color = colors[i % len(colors)]
        ls = linestyles[i % len(linestyles)]
        mk = markers[i % len(markers)]
        xs_reg, ys_reg, xs_het, ys_het = [], [], [], []
        for j, run in enumerate(runs):
            models = run.get("models")
            if not isinstance(models, list):
                continue
            for m in models:
                if isinstance(m, dict) and m.get("model") == target:
                    val = m.get(metric)
                    if val is None:
                        continue
                    if isinstance(val, float) and pd.isna(val):
                        continue
                    y = float(val)
                    if _is_hetero(run):
                        xs_het.append(j)
                        ys_het.append(y)
                    else:
                        xs_reg.append(j)
                        ys_reg.append(y)
        if xs_reg:
            ax.plot(xs_reg, ys_reg, marker=mk, linestyle=ls, linewidth=1.6,
                    markersize=4.5, color=color, label=_short_model_name(target), zorder=3)
            all_vals.extend(ys_reg)
            plotted = True
        if xs_het:
            ax.plot(xs_het, ys_het, marker='x', linestyle='None', markersize=8,
                    markeredgewidth=1.4, color=color, alpha=0.65, zorder=4)
            all_vals.extend(ys_het)
            hetero_plotted = True

    if not plotted:
        plt.close(fig)
        return _write_no_data_trend(path, f"“{metric_label}”暂无数据")

    flat_min = min(all_vals)
    flat_max = max(all_vals)
    span = (flat_max - flat_min) or 1.0
    pad = span * 0.20
    lo = flat_min - pad
    hi = flat_max + pad
    if hi > 1.0 and flat_max <= 1.0:
        hi = min(1.05, hi)
    # 若数据最小值为 0，保留 lo 为负的底部呼吸空间，避免 0 值曲线（如幻觉率≈0）贴死 X 轴无法辨认
    ax.set_ylim(lo, hi)

    # X 轴：数字位置 + 简化标签；标签过多时抽稀，避免重叠
    n = len(runs)
    if n > 12:
        step = (n + 11) // 12
        shown = [lab if (k % step == 0) else "" for k, lab in enumerate(labels)]
    else:
        shown = labels
    ax.set_xticks(x_pos)
    # 序号标签较短，无需再旋转；水平排列同时释放底部空间、扩大绘图区
    ax.set_xticklabels(shown, fontsize=12)

    ax.set_ylabel(metric_label, fontsize=15, color="#efece6", labelpad=10)
    note = "（† = truthful_qa 异构基准，× 标记）" if hetero_plotted else ""
    ax.set_title(f'质量趋势 - {metric_label}{note}' if cjk_font else f'Quality Trend - {metric}{note}', fontsize=18, pad=16, color="#efece6")
    ax.tick_params(colors="#807c8c", labelsize=12)
    for _sp in ax.spines.values():
        _sp.set_color("#34313f")
    ax.grid(True, linestyle='--', alpha=0.35, color="#34313f")
    if hetero_plotted:
        ax.plot([], [], marker='x', linestyle='None', markersize=8, markeredgewidth=1.4,
                color='0.55', label='异构基准(truthful_qa)')
    # 图例移到图下方横排：绘图区不再为外侧图例预留 36% 宽度
    n_legend = len(ax.get_legend_handles_labels()[1])
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=min(4, max(1, n_legend)), fontsize=12,
              facecolor="#141f28", edgecolor="#34313f", labelcolor="#efece6", framealpha=0.9)
    # 底部预留给下方图例（X 轴标签已改为水平短序号，不再需要额外的标签旋转空间）
    fig.subplots_adjust(left=0.10, bottom=0.26, right=0.97, top=0.90)
    if os.path.exists(path):
        os.remove(path)
    # 不用 bbox_inches='tight'：tight 会裁掉左右边距使实际输出比例漂移（1.61→1.83），
    # 导致与柱状图比例不一致；subplots_adjust 已预留全部内容空间，固定输出 1500x930（比例 1.613）
    # 背景与卡片 #141f28 相同，PNG 与卡片融为一体（对齐柱状图的融合模式）
    plt.savefig(path, dpi=150, facecolor="#141f28")
    plt.close(fig)
    return path


def draw_summary_chart(result, path):
    """从 result 生成多模型对比柱状图（演示数据可视化，独立于核心流程）"""
    cjk = _resolve_cjk_font()
    use_cjk = cjk is not None
    if use_cjk:
        plt.rcParams['font.sans-serif'] = [cjk] + CJK_FONT_CANDIDATES + ['DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    summary = [s for s in result.get("summary", []) if s.get("faithfulness") is not None]
    if not summary:
        return None

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

    # 宽高比与质量趋势图精确一致（10:6.2 = 1.613）：12/1.613=7.44；背景与卡片 #141f28 融合
    fig, ax = plt.subplots(figsize=(12, 7.44), facecolor="#141f28")
    ax.set_facecolor("#141f28")
    if len(summary) > 1:
        n = len(summary)
        for i, item in enumerate(summary):
            scores = _scores_of(item)
            offset = (i - n / 2 + 0.5) * width
            bars = ax.bar(x + offset, scores, width, label=_short_model_name(item['model']), color=colors[i % len(colors)])
            for bar, score in zip(bars, scores):
                ax.annotate(f'{score:.2f}', xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                            xytext=(0, 5), textcoords="offset points", ha='center', va='bottom', fontsize=12, color="#efece6")
        # 图例移出主绘图区，横排置于坐标轴下方，数据区全部让给柱体
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.10), ncol=len(summary), fontsize=10.5, borderpad=0.45, handlelength=1.2, handletextpad=0.6, columnspacing=1.2,
                  facecolor="#141f28", edgecolor="#34313f", labelcolor="#efece6", framealpha=0.9)
    else:
        scores = _scores_of(summary[0])
        bars = ax.bar(x, scores, color=colors, width=0.5)
        for bar, score in zip(bars, scores):
            ax.annotate(f'{score:.2f}', xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 5), textcoords="offset points", ha='center', va='bottom', fontsize=13, color="#efece6")

    ax.set_ylim(0, 1.08)
    ax.set_title('模型评测能力分析' if use_cjk else 'Model Capability Analysis', fontsize=20, pad=10, color="#efece6")
    ax.set_ylabel('Score', fontsize=15, color="#efece6", labelpad=6)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#efece6")
    ax.tick_params(colors="#c0bacd", labelsize=13)
    for _sp in ax.spines.values():
        _sp.set_color("#34313f")
    ax.grid(axis='y', linestyle='--', alpha=0.35, color="#34313f")
    # 绘图区最大化：收紧左右/顶部边距；图例已移出，底部只留图例 + X 轴标签空间
    ax.margins(x=0.01)
    fig.subplots_adjust(left=0.055, right=0.99, top=0.93, bottom=0.15)
    if os.path.exists(path):
        os.remove(path)
    # 不用 bbox_inches='tight'：固定输出 1800x1116（比例 1.613），与趋势图完全一致
    plt.savefig(path, dpi=150, facecolor="#141f28")
    plt.close()
    return path


def placeholder_chart(path, text="暂无评测数据，请先执行评测"):
    """生成一张提示占位图，作为图表组件初始值，避免空值渲染红框"""
    cjk = _resolve_cjk_font()
    if cjk:
        plt.rcParams['font.sans-serif'] = [cjk] + CJK_FONT_CANDIDATES + ['DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
        text = "No evaluation data yet. Please run an evaluation first."
    plt.rcParams['axes.unicode_minus'] = False
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor="#141f28")
    ax.set_facecolor("#141f28")
    ax.axis('off')
    ax.text(0.5, 0.5, text, ha='center', va='center', fontsize=16, color='#807c8c')
    fig.savefig(path, dpi=120, bbox_inches='tight', facecolor="#141f28")
    plt.close(fig)
    return path
