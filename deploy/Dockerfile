# —— 前端：交易顾问 ——
# 可通过 --build-arg 覆盖基础镜像（国内可设 DOCKER_MIRROR=docker.1ms.run）
# 前端用 BUILDPLATFORM（本机 arch）编译静态资源，避免 Apple Silicon 上 QEMU 跑 npm 极慢；
# 仅最终 runtime 按 TARGETPLATFORM（默认 linux/amd64）构建。
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS fe-advisor
WORKDIR /src
COPY frontend-advisor/package.json frontend-advisor/package-lock.json ./
RUN npm ci
COPY frontend-advisor/ ./
RUN npm run build

# —— 前端：数据后台 ——
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS fe-explorer
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build -- --base /explorer/

# —— 运行镜像：API + 静态资源 ——
FROM ${PYTHON_IMAGE} AS runtime

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    STATIC_ROOT=/app/static \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libpango-1.0-0 \
        libcairo2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
# BuildKit 缓存 pip，二次构建更快；国内默认走阿里云 PyPI
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt \
    && scrapling install

COPY backend/app ./app
COPY backend/data ./data

COPY --from=fe-advisor /src/dist ./static/advisor
COPY --from=fe-explorer /src/dist ./static/explorer

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
