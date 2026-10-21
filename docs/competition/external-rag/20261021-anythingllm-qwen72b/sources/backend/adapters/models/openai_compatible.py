"""OpenAI 兼容协议模型适配器（DeepSeek / Qwen / GLM 等）。

构建逻辑与 main.get_llm 逐行一致（不改变 ChatOpenAI 参数），
仅超时/重试常量改从 backend.config 读取。
"""
import os

import httpx
from langchain_openai import ChatOpenAI

from backend import config
from .base import ModelAdapter


class OpenAICompatibleAdapter(ModelAdapter):
    """封装与 main.get_llm 完全一致的 ChatOpenAI 构建逻辑。"""

    def __init__(self, model_cfg):
        self.model_cfg = model_cfg

    def build(self):
        model_cfg = self.model_cfg
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
            timeout=config.LLM_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5
            )
        )
        # 阿里云百炼的 GLM 模型默认开启深度思考，关闭以降低 reasoning token 与耗时
        extra_body = (
            {"enable_thinking": False}
            if model_cfg.get("provider") == "bailian" or model_cfg["name"].lower().startswith("glm")
            else None
        )
        return ChatOpenAI(
            model=model_cfg["name"],
            base_url=model_cfg["base_url"],
            api_key=api_key,
            temperature=0,
            max_retries=config.LLM_MAX_RETRIES,
            timeout=config.LLM_TIMEOUT,
            http_client=http_client,
            extra_body=extra_body
        )
