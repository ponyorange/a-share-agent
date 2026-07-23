#!/usr/bin/env bash
# 将本应用打包为 Docker 镜像，并把镜像包与 compose 部署文件输出到 dist/
#
# 用法：
#   ./scripts/package-docker.sh
#   IMAGE_TAG=1.0.0 ./scripts/package-docker.sh
#   PLATFORM=linux/arm64 ./scripts/package-docker.sh   # Apple Silicon 本机调试
#   DOCKER_MIRROR=docker.1ms.run ./scripts/package-docker.sh
#   ./scripts/package-docker.sh --skip-build   # 仅导出已有镜像与部署文件
#
# 默认 PLATFORM=linux/amd64（绿联 NAS / 常见 x86 服务器）。
# 若在 arm64 Mac 上打出 arm 镜像再拿到 Intel NAS，会报：exec format error
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="${ROOT}/dist"
DEPLOY="${ROOT}/deploy"
DOCKERFILE="${DEPLOY}/Dockerfile"

IMAGE_NAME="${IMAGE_NAME:-share-data}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M)}"
FULL_TAG="${IMAGE_NAME}:${IMAGE_TAG}"
LATEST_TAG="${IMAGE_NAME}:latest"
# 绿联 NAS（Intel）等 x86 机器请用 amd64；本机 Apple Silicon 调试可改 arm64
PLATFORM="${PLATFORM:-linux/amd64}"

SKIP_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "未知参数: $arg" >&2
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未找到 docker，请先安装 Docker。" >&2
  exit 1
fi

if [[ ! -f "$DOCKERFILE" ]]; then
  echo "错误: 缺少 ${DOCKERFILE}" >&2
  exit 1
fi

mkdir -p "$DIST"
# 清理旧的打包产物（保留目录）
rm -f \
  "${DIST}/${IMAGE_NAME}-"*.tar.gz \
  "${DIST}/docker-compose.yml" \
  "${DIST}/.env.example" \
  "${DIST}/README.md" \
  "${DIST}/IMAGE_TAG.txt" \
  "${DIST}/PLATFORM.txt" \
  "${DIST}/load-and-run.sh"

echo "==> 镜像: ${FULL_TAG}"

# 基础镜像（可选）：DOCKER_MIRROR=docker.1ms.run 或自行指定 NODE_IMAGE / PYTHON_IMAGE
NODE_IMAGE="${NODE_IMAGE:-}"
PYTHON_IMAGE="${PYTHON_IMAGE:-}"
if [[ -n "${DOCKER_MIRROR:-}" ]]; then
  mirror="${DOCKER_MIRROR%/}"
  NODE_IMAGE="${NODE_IMAGE:-${mirror}/library/node:22-bookworm-slim}"
  PYTHON_IMAGE="${PYTHON_IMAGE:-${mirror}/library/python:3.12-slim-bookworm}"
fi
NODE_IMAGE="${NODE_IMAGE:-node:22-bookworm-slim}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim-bookworm}"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "==> 构建镜像…"
  echo "    PLATFORM=${PLATFORM}"
  echo "    NODE_IMAGE=${NODE_IMAGE}"
  echo "    PYTHON_IMAGE=${PYTHON_IMAGE}"
  echo "    （前端本机编译；pip 走国内镜像，避免 QEMU+官方源过慢）"
  export DOCKER_BUILDKIT=1
  PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
  PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
  if docker buildx version >/dev/null 2>&1; then
    docker buildx build \
      --platform "$PLATFORM" \
      --load \
      --progress=plain \
      -f "$DOCKERFILE" \
      --build-arg "NODE_IMAGE=${NODE_IMAGE}" \
      --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}" \
      --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
      --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
      -t "$FULL_TAG" \
      -t "$LATEST_TAG" \
      "$ROOT"
  else
    docker build \
      --platform "$PLATFORM" \
      --progress=plain \
      -f "$DOCKERFILE" \
      --build-arg "NODE_IMAGE=${NODE_IMAGE}" \
      --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}" \
      --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
      --build-arg "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}" \
      -t "$FULL_TAG" \
      -t "$LATEST_TAG" \
      "$ROOT"
  fi
else
  if ! docker image inspect "$FULL_TAG" >/dev/null 2>&1; then
    echo "错误: 本地不存在镜像 ${FULL_TAG}，请去掉 --skip-build" >&2
    exit 1
  fi
  echo "==> 跳过构建，使用已有镜像 ${FULL_TAG}"
fi

TAR_NAME="${IMAGE_NAME}-${IMAGE_TAG}.tar.gz"
TAR_PATH="${DIST}/${TAR_NAME}"

echo "==> 导出镜像 → dist/${TAR_NAME}"
docker save "$FULL_TAG" | gzip -1 > "$TAR_PATH"

echo "==> 写入 compose 部署文件…"
cp "${DEPLOY}/.env.example" "${DIST}/.env.example"
cp "${DEPLOY}/README.md" "${DIST}/README.md"

# 固化本次镜像标签（与 core-service 部署风格一致）
cat > "${DIST}/docker-compose.yml" <<EOF
services:
  share-data:
    # 直接使用本地已 load 的镜像
    image: ${FULL_TAG}
    pull_policy: never
    container_name: share-data-app
    restart: always
    ports:
      - "8000:8000"

    # 线上秘钥文件（服务器本地文件，不进镜像）
    env_file:
      - .env

    environment:
      - STATIC_ROOT=/app/static
      - PORT=8000
      - CORS_ORIGINS=*

  committee-worker:
    # 复用应用镜像和外部 Redis；不会在本机创建 Redis 服务。
    image: ${FULL_TAG}
    pull_policy: never
    container_name: share-data-committee-worker
    restart: on-failure

    env_file:
      - .env

    command:
      - python
      - -m
      - app.advisor.committee.worker
EOF

printf '%s\n' "$FULL_TAG" > "${DIST}/IMAGE_TAG.txt"
printf '%s\n' "$PLATFORM" > "${DIST}/PLATFORM.txt"

cat > "${DIST}/load-and-run.sh" <<'EOF'
#!/usr/bin/env bash
# 在部署机：加载镜像并启动（需已配置 .env）
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

shopt -s nullglob
TARS=(share-data-*.tar.gz)
if [[ ${#TARS[@]} -eq 0 ]]; then
  echo "未找到 share-data-*.tar.gz" >&2
  exit 1
fi
echo "加载镜像: ${TARS[0]}"
gunzip -c "${TARS[0]}" | docker load

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已生成 .env，请从密钥系统配置 MONGODB_URI / JWT_SECRET / LLM_ENCRYPTION_KEY；启用委员会时还需配置外部 Redis。" >&2
  exit 1
fi

docker compose up -d
echo "已启动。健康检查: curl -s http://127.0.0.1:8000/api/health"
EOF
chmod +x "${DIST}/load-and-run.sh"

SIZE="$(du -h "$TAR_PATH" | awk '{print $1}')"
echo
echo "打包完成 → ${DIST}/"
echo "  镜像包:     ${TAR_NAME} (${SIZE})"
echo "  镜像标签:   ${FULL_TAG}"
echo "  目标架构:   ${PLATFORM}"
echo "  编排:       docker-compose.yml"
echo "  环境模板:   .env.example"
echo "  说明:       README.md"
echo "  一键脚本:   load-and-run.sh"
echo
echo "部署机示例:"
echo "  scp -r dist/ user@host:/opt/share-data/"
echo "  cd /opt/share-data && ./load-and-run.sh"
