# RAG大模型幻觉自动化评测工具

> 面向大模型 RAG 应用“幻觉”痛点的轻量化自动化评测工具，基于 LangChain、Ragas 和 Chroma。

## 📖 项目背景
当前基于 RAG（检索增强生成）的大模型应用广泛落地，但“幻觉”（模型一本正经地胡说八道）严重影响可靠性。传统的人工抽检效率低且不可复现。本项目旨在通过自动化手段，快速评测 RAG 系统的忠实度和检索精度。

## ✨ 核心功能
1. **知识库加载与切分**：读取本地 txt 文档，自动分段存入 Chroma 向量数据库。
2. **自动生成测试集**：利用大模型根据知识库自动出题，解决人工想测试用例的烦恼。
3. **RAG 链路评测**：执行检索和问答，收集上下文和回答。
4. **量化指标评测**：使用 Ragas 计算 `faithfulness`（幻觉检测核心）、`context_precision`（检索精度）。
5. **报告导出**：控制台打印结果，并自动导出 CSV 报告。

## 🛠️ 技术架构
- 语言：Python 3.11.9
- 编排框架：LangChain
- 评测框架：Ragas
- 向量库：Chroma
- Embedding：HuggingFace (BAAI/bge-small-zh-v1.5)
- 大模型：DeepSeek (兼容 OpenAI 接口)

## 🚀 快速开始
### 1. 环境准备
```bash
# 创建并激活虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt