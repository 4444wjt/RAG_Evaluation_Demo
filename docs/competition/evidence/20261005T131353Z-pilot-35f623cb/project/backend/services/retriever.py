"""向量库构建（与 main.build_vector_store 一致）。"""
import os
import shutil

from langchain_community.vectorstores import Chroma

from backend.config import REUSE_VECTOR_DB, CHROMA_DIR


def build_vector_store(chunks, embeddings):
    """构建向量库，支持复用已有库"""
    if REUSE_VECTOR_DB and os.path.exists(CHROMA_DIR):
        print("ℹ️ 复用已有向量库")
        return Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=embeddings
        )

    # 强制重建
    shutil.rmtree(CHROMA_DIR, ignore_errors=True)
    return Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )
