# 平台小本子。镜像只带这一页要用的包，不进问答服务那份大依赖。
# 构建在 GitHub 上进行，阿里云那台 2 核机器只负责把镜像跑起来。

FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY desk/requirements.txt /tmp/reqs.txt
RUN pip install --no-cache-dir --timeout 180 --index-url "${PIP_INDEX_URL}" -r /tmp/reqs.txt \
    && useradd --create-home --uid 10002 notebook

COPY desk/ ./desk/
COPY backend/desk_log.py backend/logging_config.py ./backend/

USER notebook
WORKDIR /app/desk
# 缺了登录表单的依赖时，镜像在这里就构建失败，不会传到服务器才崩。
RUN python -c "import app"

EXPOSE 8300

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8300/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8300"]
