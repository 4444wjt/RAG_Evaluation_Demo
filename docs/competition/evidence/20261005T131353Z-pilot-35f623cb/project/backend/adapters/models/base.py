"""模型适配器抽象基类。"""
from abc import ABC, abstractmethod


class ModelAdapter(ABC):
    """把模型配置转换为兼容的 LangChain ChatModel 实例的抽象接口。

    与 main.get_llm 等价：返回 ChatOpenAI 实例，构造参数保持一致。
    """

    @abstractmethod
    def build(self):
        """构建并返回兼容的 LLM 实例。"""
        raise NotImplementedError
