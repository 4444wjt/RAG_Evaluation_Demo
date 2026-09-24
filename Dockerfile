# 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量：镜像源和编码
ENV PYTHONUNBUFFERED=1
ENV HF_ENDPOINT=https://hf-mirror.com
ENV HF_HUB_ENABLE_HF_TRANSFER=1

# 复制依赖清单
COPY requirements.txt .

# 安装依赖（使用清华源加速）
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 默认运行命令
CMD ["python", "main.py"]