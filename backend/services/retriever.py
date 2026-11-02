"""向量库构建（与 main.build_vector_store 一致）。"""
import os

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

    # 通过客户端重建集合；直接删目录会破坏 Chroma 按路径缓存的进程内系统。
    previous = Chroma(persist_directory=CHROMA_DIR, embedding_function=embeddings)
    try:
        previous.delete_collection()
        return Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            persist_directory=CHROMA_DIR,
            client=previous._client,
        )
    except Exception:
        previous._client.close()
        raise
