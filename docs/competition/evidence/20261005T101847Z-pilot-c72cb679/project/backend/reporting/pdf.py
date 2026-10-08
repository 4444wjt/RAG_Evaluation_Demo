"""PDF 报告导出（与 main.export_report_pdf 一致）。"""
import os

import matplotlib.pyplot as plt

from backend import config
from backend.core.utils import _resolve_cjk_font, CJK_FONT_CANDIDATES
from . import build_display_df


def export_report_pdf(result, path):
    """导出评测报告为 PDF（汇总表 + 结果图表，零新依赖）"""
    from matplotlib.backends.backend_pdf import PdfPages

    cjk = _resolve_cjk_font()
    if cjk:
        plt.rcParams['font.sans-serif'] = [cjk] + CJK_FONT_CANDIDATES + ['DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    disp = build_display_df(result["summary_df"])
    fig1, ax1 = plt.subplots(figsize=(10, 3 + 0.5 * len(disp)))
    ax1.axis('off')
    ax1.set_title('RAG 幻觉评测报告', fontsize=16, pad=16)
    tbl = ax1.table(cellText=disp.values, colLabels=disp.columns, cellLoc='center', loc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(11)
    tbl.scale(1, 1.8)

    pdf = PdfPages(path)
    pdf.savefig(fig1)
    plt.close(fig1)

    chart = result.get("chart_path") or config.CHART_PATH
    if os.path.exists(chart):
        img = plt.imread(chart)
        fig2 = plt.figure(figsize=(10, 6))
        plt.imshow(img)
        plt.axis('off')
        pdf.savefig(fig2)
        plt.close(fig2)

    pdf.close()
    return path
