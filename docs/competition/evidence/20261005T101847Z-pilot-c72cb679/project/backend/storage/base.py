"""存储层抽象基类。"""
from abc import ABC, abstractmethod


class HistoryStore(ABC):
    """评测历史记录的抽象存储接口。"""

    @abstractmethod
    def load(self, path=None):
        """加载历史记录，容错返回列表。"""
        raise NotImplementedError

    @abstractmethod
    def save(self, record, path=None):
        """追加一条记录。"""
        raise NotImplementedError

    @abstractmethod
    def to_dataframe(self, path=None):
        """把历史记录展平成 DataFrame。"""
        raise NotImplementedError
