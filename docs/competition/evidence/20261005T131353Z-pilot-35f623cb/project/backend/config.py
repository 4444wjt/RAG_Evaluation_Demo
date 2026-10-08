"""后端配置：常量、模型配置、默认配置。

从 main.py「全局常量配置」区原样迁移，值与行为保持一致；
main.py 保留旧定义作为兼容入口，本模块可独立 import、不依赖 main。
"""
import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# 项目根目录（backend 的父目录），与 main.py 的 BASE_DIR 指向同一位置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 从 .env 加载环境变量（API 密钥、模型名、base_url 等）
if load_dotenv is not None:
    load_dotenv(os.path.join(BASE_DIR, ".env"))

# 核心路径配置
DOC_PATH = os.path.join(BASE_DIR, "demo.txt")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
SUMMARY_OUTPUT = os.path.join(BASE_DIR, "multi_model_summary.csv")
HTML_OUTPUT = os.path.join(BASE_DIR, "ragas_report.html")
CHART_PATH = os.path.join(BASE_DIR, "result_chart.png")
HISTORY_PATH = os.path.join(BASE_DIR, "eval_history.json")
TREND_CHART_PATH = os.path.join(BASE_DIR, "trend_chart.png")

# 模型配置
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

# 若项目内 models/ 目录已放置 embedding 模型，则自动改用本地路径，避免联网下载
_LOCAL_EMBED = os.path.join(BASE_DIR, "models", "bge-small-zh-v1.5")
if os.path.exists(_LOCAL_EMBED):
    EMBEDDING_MODEL = _LOCAL_EMBED

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
# 幻觉溯源与归因开关：每题额外 1 次 LLM 调用；设环境变量 RAG_ENABLE_TRACE=0 可关闭以节省 token
ENABLE_TRACE = os.environ.get("RAG_ENABLE_TRACE", "1") != "0"

# ====================== 多模型配置（安全版：环境变量读取密钥） ======================
MODELS_CONFIG = [
    {
        "name": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key_env": "OPENAI_API_KEY"
    },
    {
        "name": os.environ.get("QWEN_MODEL", "Pro/Qwen/Qwen2.5-7B-Instruct"),
        "base_url": os.environ.get("QWEN_BASE_URL", "https://api.siliconflow.cn/v1"),
        "api_key_env": "SILICONFLOW_API_KEY"
    },
    {
        "name": "glm-5.2-fast-preview",
        "provider": "bailian",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "display_name": "GLM-5.2-Fast"
    },
]
