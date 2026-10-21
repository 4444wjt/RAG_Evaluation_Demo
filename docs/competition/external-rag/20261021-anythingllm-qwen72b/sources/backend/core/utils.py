"""通用工具函数。

从 main.py 原样迁移（不优化逻辑、保持异常行为与默认值一致）；
main.py 保留旧函数作为兼容入口，本模块可独立 import。
"""
import json
import os
import re

import pandas as pd


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


def _safe_mean(series_like):
    """计算 pandas Series 的均值，返回四舍五入后的 float 或 None"""
    try:
        val = pd.Series(series_like).dropna().mean()
        return round(float(val), 4) if pd.notna(val) else None
    except Exception:
        return None


# 拒答判定的关键词（负样本期望模型拒答，而非编造答案）
REFUSAL_KEYWORDS = ("无法回答", "未提及", "没有相关", "无法提供", "未提供", "不存在", "无法确认")


def _is_refusal(answer):
    """判断模型回答是否为拒答：命中文档无法回答的典型措辞即视为拒答"""
    if answer is None:
        return False
    return any(kw in str(answer) for kw in REFUSAL_KEYWORDS)


# 模型展示名：图例与新报告统一名称，原始模型 ID 保持不变
MODEL_DISPLAY_MAP = {
    "deepseek-chat": "DeepSeek-V4.1-Flash",
    "deepseek-flash": "DeepSeek-V4.1-Flash",
    "Pro/Qwen/Qwen2.5-7B-Instruct": "Qwen2.5-7B",
    "glm-5.2-fast-preview": "GLM-5.2-Fast",
}


def _short_model_name(name):
    return MODEL_DISPLAY_MAP.get(name, name)


CJK_FONT_CANDIDATES = [
    'Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'Noto Sans CJK JP',
    'WenQuanYi Zen Hei', 'WenQuanYi Micro Hei', 'PingFang SC', 'Source Han Sans SC',
]


def _resolve_cjk_font():
    """真实验证系统可用 CJK 字体，避免字体名存在但文件不可加载导致的脱节"""
    import matplotlib.font_manager as fm
    from matplotlib.font_manager import FontProperties
    for name in CJK_FONT_CANDIDATES:
        try:
            path = fm.findfont(FontProperties(family=name), fallback_to_default=False)
            if path and os.path.exists(path):
                return name
        except Exception:
            continue
    return None


def friendly_error_message(e):
    """把底层异常转成评委友好的中文提示，避免直接抛出技术堆栈"""
    msg = str(e) or e.__class__.__name__
    low = msg.lower()
    if "api" in low or "key" in low or "auth" in low or "401" in msg or "402" in msg or "403" in msg or "429" in msg:
        return "API 调用失败：请检查 .env 中的 API 密钥是否有效、账户是否有额度。"
    if "timeout" in low or "timed out" in low:
        return "请求超时：模型响应过慢，请稍后重试或检查网络连通性。"
    if "所有模型评测全部失败" in msg or "全部失败" in msg:
        return "所有模型评测均失败：请检查模型配置与 API 连通性后重试。"
    if "不存在" in msg or "not found" in low or "no such file" in low:
        return "文件错误：请确认文档/测试集文件存在且路径正确。"
    if "格式" in msg or "json" in low or "csv" in low or "parse" in low or "decode" in low or "不支持的" in msg:
        return "格式错误：请检查上传文件格式（文档 .txt/.pdf/.docx/.md，测试集 .json/.csv 且字段完整）。"
    return f"评测过程中出现异常：{msg[:140]}"
