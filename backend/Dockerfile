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
# CPU 版 PyTorch：容器无 GPU，避免 pip 默认拉 CUDA 大包
RUN pip install --no-cache-dir --timeout 120 torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --timeout 120 -r requirements.txt

# 复制后端代码（所有 .py 文件）
COPY backend/ ./backend/

# 复制知识库 PDF 文件
COPY data/ ./data/

# 创建 ChromaDB 持久化目录
RUN mkdir -p /app/backend/chroma_db_data

# 8000 给 FastAPI，8501 给旧版 Streamlit 界面
EXPOSE 8000 8501

# 默认工作目录与 compose 一致；直接 docker run 时也从 backend 启动
WORKDIR /app/backend

# 默认起 FastAPI；旧版 Streamlit 界面由 compose 的 legacy profile 覆盖 command
CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
