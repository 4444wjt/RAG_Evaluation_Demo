"""RAG 问答（与 main.rag_answer 一致）。"""


def rag_answer(question, retriever, llm):
    """RAG检索问答，强制输出完整句子"""
    docs = retriever.invoke(question)
    contexts = [d.page_content for d in docs]

    prompt = f"""
    请根据以下参考资料，用一段完整的、详细的中文句子回答问题。
    如果问题中的说法或前提与参考资料不一致，请依据参考资料指出并纠正，不要拒答。
    如果参考资料中没有相关信息，请直接回答“根据提供的文档，无法回答该问题。”，不要编造。

    参考资料：
    {chr(10).join(contexts)}

    问题：{question}
    答案：
    """
    answer = llm.invoke(prompt).content.strip()
    return answer, contexts
