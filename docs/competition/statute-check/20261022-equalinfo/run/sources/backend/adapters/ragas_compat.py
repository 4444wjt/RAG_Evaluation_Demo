"""Ragas 0.1.21 的中文陈述输入兼容层，不改评分或裁判提示定义。

原指标在分句后仅保留以英文句号结尾的句子。这里只让中文及中英
混排的非空片段进入原陈述抽取提示；纯英文继续使用原实现。
Web 使用独立指标与提示对象，旧 CLI 的 Ragas 全局单例保持原样。
"""
from copy import deepcopy
from importlib.metadata import version
import re
from typing import ClassVar

from ragas.llms.prompt import PromptValue
from ragas.metrics import AnswerCorrectness, Faithfulness


SUPPORTED_RAGAS_VERSION = "0.1.21"
SENTENCE_ADAPTER_VERSION = "zh-sentence-v1"

if version("ragas") != SUPPORTED_RAGAS_VERSION:
    raise RuntimeError(
        f"{SENTENCE_ADAPTER_VERSION} 仅支持 Ragas {SUPPORTED_RAGAS_VERSION}；"
        "升级依赖后需要重新验证中文句子兼容层。"
    )

# 根据实际待抽取文本判断，不能用问题语言改变英文参考答案的行为。
_CHINESE_TEXT = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3002\uff01\uff1f]"
)


def _numbered_chinese_sentences(text, segmenter):
    """复用现有分句器，保留原片段、顺序和标点，不补造陈述。"""
    assert segmenter is not None, "sentence_segmenter is not set"
    sentences = [sentence for sentence in segmenter.segment(text) if sentence.strip()]
    return "\n".join(f"{index}:{sentence}" for index, sentence in enumerate(sentences))


class ChineseAnswerCorrectness(AnswerCorrectness):
    """仅适配中文陈述输入；计算、权重、解析、重试均继承原类。"""

    sentence_adapter_version: ClassVar[str] = SENTENCE_ADAPTER_VERSION

    def __post_init__(self):
        super().__post_init__()
        self.long_form_answer_prompt = deepcopy(self.long_form_answer_prompt)
        self.correctness_prompt = deepcopy(self.correctness_prompt)

    def _create_statements_prompt(self, question: str, text: str) -> PromptValue:
        if not _CHINESE_TEXT.search(text):
            return super()._create_statements_prompt(question, text)
        return self.long_form_answer_prompt.format(
            question=question,
            answer=text,
            sentences=_numbered_chinese_sentences(text, self.sentence_segmenter),
        )


class ChineseFaithfulness(Faithfulness):
    """仅适配中文陈述输入；上下文支持判定及计算均继承原类。"""

    sentence_adapter_version: ClassVar[str] = SENTENCE_ADAPTER_VERSION

    def __post_init__(self):
        super().__post_init__()
        self.statement_prompt = deepcopy(self.statement_prompt)
        self.nli_statements_message = deepcopy(self.nli_statements_message)

    def _create_statements_prompt(self, row: dict) -> PromptValue:
        text = row["answer"]
        if not _CHINESE_TEXT.search(text):
            return super()._create_statements_prompt(row)
        return self.statement_prompt.format(
            question=row["question"],
            answer=text,
            sentences=_numbered_chinese_sentences(text, self.sentence_segmenter),
        )
