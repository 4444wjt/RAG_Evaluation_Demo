"""模型适配器注册表：按配置选择适配器，并提供与 main.get_llm 等价的入口。"""
from .base import ModelAdapter
from .openai_compatible import OpenAICompatibleAdapter


def adapter_for(model_cfg) -> ModelAdapter:
    """为给定模型配置选择适配器（当前全部为 OpenAI 兼容协议）。"""
    return OpenAICompatibleAdapter(model_cfg)


def build_llm(model_cfg):
    """等价于 main.get_llm 的入口：构建并返回兼容的 LLM 实例。"""
    return adapter_for(model_cfg).build()
