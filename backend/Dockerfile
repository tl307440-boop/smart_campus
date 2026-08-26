# ==========================================
# 智悦校园 · 后端镜像（FastAPI，legacy profile 下跑 Streamlit）
# ==========================================

# ---- 构建阶段：装依赖，编译工具链只留在这一层 ----
FROM python:3.12-slim AS builder

# 部分包没有现成 wheel，需要现场编译
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.lock .
# 装全量锁定版本，保证每次构建装到的是同一组包。
# 锁里 torch 是 +cpu 变体（容器无 GPU，避免拉进几 GB 的 CUDA 包），
# 这个变体只在 PyTorch 官方索引上有，所以要多给一个 index。
# 改依赖请编辑 requirements.txt 后重新生成：
#   uv pip compile requirements.txt --python-platform linux --python-version 3.12 \
#       --torch-backend cpu --output-file requirements.lock
RUN pip install --no-cache-dir --timeout 120 \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.lock

# ---- 运行阶段：只带虚拟环境和代码，不带编译工具链 ----
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY backend/ ./backend/
COPY data/ ./data/
# 规章维基（knowledge.py 按 backend/../wiki 定位，必须落在 /app/wiki）
COPY wiki/ ./wiki/

# 非 root 运行：上传接口 + pypdf 解析是真实攻击面，出事时限制影响范围。
# 注意：compose 把 ./data 以 bind mount 挂进来，宿主机上该目录需要
# 对 uid 10001 可写，否则上传会失败（Linux 服务器执行一次
# `sudo chown -R 10001:10001 data` 即可；Docker Desktop 无此问题）。
RUN useradd --create-home --uid 10001 campus \
    && mkdir -p /app/backend/chroma_db_data /app/data/uploads \
    && chown -R campus:campus /app

USER campus

# 8000 给 FastAPI，8501 给旧版 Streamlit 界面
EXPOSE 8000 8501

# 首次启动要把维基与预置文档跑一遍向量化，start-period 给够
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

# 默认工作目录与 compose 一致；直接 docker run 时也从 backend 启动
WORKDIR /app/backend

# 默认起 FastAPI；旧版 Streamlit 界面由 compose 的 legacy profile 覆盖 command
CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
