# 基础镜像
FROM python:3.11-slim

# 非交互式安装 + 关闭 pip 缓存 + 国内 HuggingFace 镜像
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    HF_ENDPOINT=https://hf-mirror.com

# 设置工作目录
WORKDIR /app

# 安装中文字体（图表渲染需要，消除 Glyph 缺失告警）
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖清单并安装（清华源加速）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 预留 Gradio Web 界面端口
EXPOSE 7860

# 默认启动 Web 界面
CMD ["python", "main.py", "--web", "--port", "7860"]