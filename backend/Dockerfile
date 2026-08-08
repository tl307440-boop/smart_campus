# ==========================================
# 智悦校园 · Streamlit 应用 Docker 镜像
# ==========================================

# 基础镜像：Python 3.12 轻量版
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（pypdf 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层（代码变了不用重装依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码（所有 .py 文件）
COPY backend/ ./backend/

# 复制知识库 PDF 文件
COPY data/ ./data/

# 创建 ChromaDB 持久化目录
RUN mkdir -p /app/backend/chroma_db_data

# 暴露 Streamlit 端口
EXPOSE 8501

# 默认工作目录与 compose 一致；直接 docker run 时也从 backend 启动
WORKDIR /app/backend

# --server.headless=true  不打开浏览器
# --server.address=0.0.0.0  允许外部访问
# --server.port=8501  指定端口
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.headless=true", \
     "--server.address=0.0.0.0", \
     "--server.port=8501"]
