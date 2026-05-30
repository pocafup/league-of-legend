# ── 阶段 1：安装依赖（利用 Docker 层缓存）────────────────────────────────────
FROM python:3.13-slim AS builder

# 拷入 uv（不依赖 pip/setuptools）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 先只拷依赖描述文件，lock 文件未变时整层可命中缓存
COPY pyproject.toml uv.lock ./

# 安装 server 端依赖，不含 agent group（psutil）
# UV_COMPILE_BYTECODE=1 → 编译 .pyc，容器启动更快
RUN UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    uv sync --frozen --no-group agent --no-install-project


# ── 阶段 2：运行镜像 ──────────────────────────────────────────────────────────
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 从 builder 拷入已安装的 venv
COPY --from=builder /app/.venv /app/.venv

# 拷入应用代码（.dockerignore 排除了不需要的文件）
COPY . .

# 确保缓存目录存在（volume 挂载前的保底）
RUN mkdir -p /app/cache/stats

# 不向宿主机 publish，Caddy 通过 Docker 网络直连
EXPOSE 8765

# 把 venv 的 bin 放前面，直接用 uvicorn，不经 uv run 包装（更干净的 PID 1）
ENV PATH="/app/.venv/bin:$PATH"

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/draft')" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765"]
