# main.py
# RAG 大模型幻觉自动化评测工具（学生轻量 Demo）
# 功能：读取本地 txt -> 切分 -> 存入 Chroma -> 自动生成测试问答 -> RAG 问答 -> Ragas 评测 -> 导出 CSV

import os
import json
import shutil
import pandas as pd
from datasets import Dataset

from langchain_openai import ChatOpenAI
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision


# ==================== 全局配置 ====================
TXT_PATH = "demo.txt"                 # 本地测试文本路径
CHROMA_DIR = "./chroma_db"            # Chroma 向量库持久化目录
CSV_PATH = "ragas_report.csv"         # 评测报告导出路径
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"  # HuggingFace 中文 embedding 模型
LLM_MODEL = "deepseek-chat"           # 修改为 DeepSeek 的模型名称
TOP_K = 3                             # 检索返回的上下文数量
TEST_QA_NUM = 3                       # 自动生成测试问答的数量


def get_llm():
    """
    创建 LangChain LLM 对象。
    Ragas 计算 faithfulness、answer_relevancy 等指标时，需要 LLM 作为裁判。
    这里从环境变量读取 API Key 和 Base URL，方便切换不同厂商。
    """
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")  # 例如 DeepSeek: https://api.deepseek.com

    if not api_key:
        raise ValueError("请先设置环境变量 OPENAI_API_KEY，例如：set OPENAI_API_KEY=你的key")

    return ChatOpenAI(
        model=LLM_MODEL,
        temperature=0,          # 评测场景希望输出稳定，温度设为 0
        api_key=api_key,
        base_url=base_url
    )


def load_and_split(txt_path):
    """
    读取本地 txt 文件，并按固定长度切分成文本块。
    这里使用 RecursiveCharacterTextSplitter，适合普通文本的最小 Demo。
    """
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,         # 每个文本块最大 500 字符
        chunk_overlap=50        # 相邻块重叠 50 字符，避免上下文断裂
    )
    chunks = splitter.split_text(text)
    return chunks


def build_vectorstore(chunks, embeddings):
    """
    将文本块存入 Chroma 向量数据库。
    每次运行前清空旧目录，避免重复插入导致检索结果混乱。
    """
    shutil.rmtree(CHROMA_DIR, ignore_errors=True)  # 删除旧向量库目录

    vectorstore = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )
    return vectorstore


def generate_testset(chunks, llm, num=3):
    """
    自动生成测试问答数据集。
    做法：取前 num 个文本块，让 LLM 根据每个块生成一个问题和标准答案。
    注意：这是最小 Demo 的简化做法，真实项目可用 Ragas TestsetGenerator。
    """
    testset = []

    for chunk in chunks[:num]:
        prompt = f"""请根据以下文本生成一个测试问答对。
要求：
1. 问题必须基于文本内容，不要编造。
2. 答案要简洁准确。
3. 严格按 JSON 格式输出，不要添加其他内容：
{{"question": "问题", "ground_truth": "答案"}}

文本：
{chunk}
"""
        resp = llm.invoke(prompt).content.strip()

        try:
            # 兼容 LLM 可能返回 markdown 代码块的情况
            if resp.startswith("```"):
                resp = resp.strip("`").strip()
                if resp.startswith("json"):
                    resp = resp[4:].strip()

            qa = json.loads(resp)
            testset.append({
                "question": qa["question"],
                "ground_truth": qa["ground_truth"]
            })
        except Exception as e:
            print(f"[警告] 解析 LLM 输出失败：{e}")
            print(f"原始输出：{resp}")

    return testset


def rag_answer(question, vectorstore, llm, top_k=3):
    """
    执行一次 RAG 问答：
    1. 用问题在 Chroma 中检索 top_k 个相关文本块；
    2. 把检索到的上下文和问题拼成 prompt；
    3. 调用 LLM 生成答案。
    返回：answer 和 contexts。
    """
    docs = vectorstore.similarity_search(question, k=top_k)
    contexts = [doc.page_content for doc in docs]
    context_text = "\n\n".join(contexts)

    # 修改点：强调“用完整、详细的句子回答”，避免 Ragas 无法提取 statements 导致 faithfulness 为 nan
    prompt = f"""请仅根据以下上下文，用完整、详细的句子回答问题。
如果上下文没有相关信息，请回答“不知道”。

上下文：
{context_text}

问题：{question}
答案："""

    answer = llm.invoke(prompt).content.strip()
    return answer, contexts


def main():
    print("=== RAG 大模型幻觉自动化评测工具 ===")

    # 1. 初始化 HuggingFace Embedding 和 LLM
    print("正在加载 HuggingFace Embedding 模型...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    print("正在初始化 LLM...")
    llm = get_llm()

    # 2. 读取并切分本地 txt
    print("1. 加载并切分本地 txt 文档...")
    chunks = load_and_split(TXT_PATH)
    print(f"共切分 {len(chunks)} 个文本块")

    # 3. 存入 Chroma 向量库
    print("2. 存入 Chroma 向量数据库...")
    vectorstore = build_vectorstore(chunks, embeddings)

    # 4. 自动生成测试问答数据集
    print("3. 自动生成测试问答数据集...")
    testset = generate_testset(chunks, llm, num=TEST_QA_NUM)
    print(f"生成 {len(testset)} 条测试问题")

    # 5. 对每个问题执行 RAG 问答，收集 Ragas 评估所需数据
    print("4. 执行 RAG 问答并收集评估数据...")
    eval_data = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": []
    }

    for item in testset:
        q = item["question"]
        gt = item["ground_truth"]

        ans, ctxs = rag_answer(q, vectorstore, llm, top_k=TOP_K)

        eval_data["question"].append(q)
        eval_data["answer"].append(ans)
        eval_data["contexts"].append(ctxs)
        eval_data["ground_truth"].append(gt)

        print(f"\n问题：{q}")
        print(f"标准答案：{gt}")
        print(f"模型回答：{ans}")

    # 6. 使用 Ragas 计算核心指标
    print("\n5. 使用 Ragas 计算评测指标...")
    dataset = Dataset.from_dict(eval_data)

    result = evaluate(
        dataset,
        metrics=[
            faithfulness,        # 忠实度：答案是否完全基于检索上下文，幻觉检测核心
            # answer_relevancy,  # 暂时禁用，因为 DeepSeek API 不支持 n>1 参数
            context_precision    # 上下文精度：检索到的上下文中有多少是真正有用的
        ],
        llm=llm,
        embeddings=embeddings
    )

    print("\n评测结果：")
    print(result)

    # 7. 导出 CSV 报告
    print(f"\n6. 导出 CSV 报告到 {CSV_PATH} ...")
    df = result.to_pandas()
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print("完成！")


if __name__ == "__main__":
    main()