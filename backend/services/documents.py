"""文档加载与切分（与 main.load_and_split_doc 一致）。"""
import os

from langchain_community.document_loaders import TextLoader, PyPDFLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.config import CHUNK_SIZE, CHUNK_OVERLAP


def load_and_split_doc(path):
    """加载并切分文档（按扩展名分流：.txt/.md 纯文本、.pdf、.docx）"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文档不存在: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        loader = PyPDFLoader(path)
    elif ext == ".docx":
        loader = Docx2txtLoader(path)
    else:
        # .txt / .md / .markdown 等纯文本统一走 TextLoader，md 无需引入重依赖 unstructured
        loader = TextLoader(path, encoding="utf-8-sig")

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", " ", ""]
    )
    chunks = loader.load_and_split(text_splitter)

    if not chunks:
        raise RuntimeError("文档切分后为空，请检查文档内容长度")
    return chunks
