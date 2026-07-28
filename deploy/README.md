# Share Data Docker 部署

本目录产物由 `scripts/package-docker.sh` 打包到仓库根目录 `dist/`。

## 产物说明

| 文件 | 说明 |
|------|------|
| `share-data-<tag>.tar.gz` | `docker save` 导出的应用镜像 |
| `share-data-sandbox-controller-<tag>.tar.gz` | `docker save` 导出的沙箱 Controller 镜像 |
| `share-data-python-sandbox-<tag>.tar.gz` | `docker save` 导出的沙箱 Runner 镜像（一次性容器使用，不作为常驻 service） |
| `docker-compose.yml` | 编排文件（`pull_policy: never`，用本地镜像） |
| `.env.example` | 秘钥 / Mongo 等环境变量模板 |
| `IMAGE_TAG.txt` | 本次打包的镜像标签 |
| `load-and-run.sh` | 加载镜像并 `compose up` |
| `README.md` | 本说明 |

## 部署步骤

### 构建发布包

从仓库根目录执行打包脚本。脚本会构建并导出三类镜像：应用、
`sandbox-controller` 和一次性 Runner；默认目标平台为 `linux/amd64`：

```bash
DOCKER_MIRROR=docker.1ms.run PLATFORM=linux/amd64 ./scripts/package-docker.sh
```

发布目录 `dist/` 可整体复制到部署机：

```bash
scp -r dist/ user@host:/opt/share-data/
```

### 从源码直接部署

若不使用发布包，也可从仓库根目录构建三类镜像。先复制 `deploy/.env.example` 为 `deploy/.env`，
填入 `MONGODB_URI`、`JWT_SECRET`、`LLM_ENCRYPTION_KEY` 和至少 32 字节的
`SANDBOX_TOKEN`；若当前 shell 未自动读取 `deploy/.env`，先导出其中变量或用
Compose 的 `--env-file deploy/.env` 选项。Runner 只作为一次性执行镜像构建，
不作为常驻 Compose service 启动：

```bash
docker build -f sandbox/runner/Dockerfile -t share-data-python-sandbox:2026-07-24 .
docker build -f sandbox/controller/Dockerfile -t share-data-sandbox-controller:2026-07-24 .
docker build -f deploy/Dockerfile -t share-data:latest .
docker compose -f deploy/docker-compose.yml up -d
```

### 在部署机启动发布包

```bash
# 1. 加载所有镜像
for image in *.tar.gz; do gunzip -c "$image" | docker load; done

# 2. 配置秘钥（不进镜像）
cp .env.example .env
# 编辑 MONGODB_URI / JWT_SECRET / SANDBOX_TOKEN 等
# SANDBOX_TOKEN 至少 32 字节，建议用：openssl rand -hex 32

# 3. 启动
docker compose up -d

# 4. 检查旧应用健康
curl -s http://127.0.0.1:8000/api/health

# 5. 启用委员会后，用登录令牌检查 Redis 与 checkpoint
curl -s -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8000/api/advisor/committee/health
```

或直接：`./load-and-run.sh`。首次运行若没有 `.env`，脚本会复制
`.env.example` 为 `.env` 后退出；填入 `MONGODB_URI`、`JWT_SECRET`、
`LLM_ENCRYPTION_KEY`、至少 32 字节的 `SANDBOX_TOKEN` 后再执行一次。

生产必须保持 `APP_ENV=production` 与 `DEV_SEED_ENABLED=0`。
`MONGODB_URI`、`JWT_SECRET`、`LLM_ENCRYPTION_KEY` 均由部署机密钥系统注入；
后两者必须是相互独立、至少 32 字节的高熵值。LLM 密钥轮换窗口可设置
`LLM_ENCRYPTION_KEY_PREVIOUS`，完成数据重加密后立即删除旧值。
`SANDBOX_TOKEN` 也必须独立生成且至少 32 字节，只用于 API 到
`sandbox-controller` 的内部鉴权。轮换时同时更新应用和 Controller 使用的
`.env`，再执行 `docker compose restart share-data sandbox-controller`。
Runner 一次性容器永远不能接收该 Token。
Compose 使用必填插值防止空 token 被静默接受；API 客户端和 Controller
进程都会拒绝少于 32 字节的 token。从仓库根目录执行
`docker compose -f deploy/docker-compose.yml config` 前，需先导出
`SANDBOX_TOKEN` 或加载 `deploy/.env`。

## 访问

| 地址 | 说明 |
|------|------|
| `http://<host>:8000/` | 交易顾问 |
| `http://<host>:8000/explorer/` | 数据后台 |
| `http://<host>:8000/api/health` | 健康检查 |

MongoDB 需自行准备；通过 `.env` 中的 `MONGODB_URI` 连接。

## 沙箱控制面

`sandbox-controller` 是持有 Docker socket 的高权限控制面，只能在 Compose
内部网络暴露给 `share-data`。不要为它配置 `ports`，不要发布到宿主机或公网。
`share-data` 只通过 `SANDBOX_URL=http://sandbox-controller:8090` 和
`SANDBOX_TOKEN` 调用 Controller；`share-data`、`committee-worker`、
`monitor-worker` 和 Runner 均不得挂载 `/var/run/docker.sock`。Controller 固定使用
`share-data-python-sandbox:2026-07-24` 作为 Runner 镜像，客户端不能传入镜像、
挂载、网络或其他容器参数。

`monitor-worker` 与主应用共用镜像，交易时段轮询 `agent_monitor_jobs` 并发邮件告警；
需在部署机 `.env` 配置 `MONGODB_URI` 与 `MAIL_*`。管理页：顾问前端 `/agent/jobs`。

真实 Docker Engine smoke test：

```bash
docker compose exec sandbox-controller python - <<'PY'
import json
import os
import urllib.request

body = {
    "code": "result = sum(row['x'] for row in datasets['demo'])",
    "datasets": {"demo": [{"x": 1}, {"x": 2}, {"x": 3}]},
    "timeout_seconds": 5,
    "memory_mb": 128,
    "max_output_bytes": 4096,
}
request = urllib.request.Request(
    "http://127.0.0.1:8090/v1/execute",
    data=json.dumps(body).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Sandbox-Token": os.environ["SANDBOX_TOKEN"],
    },
)
print(urllib.request.urlopen(request, timeout=15).read().decode())
PY
```

预期：返回 `{"ok":true,...,"result":6,...}`。

验证 Runner 无法联网：

```bash
docker compose exec sandbox-controller python - <<'PY'
import json
import os
import urllib.request

body = {
    "code": "import socket\nsocket.create_connection(('1.1.1.1', 53), 1)\nresult = 'unexpected'",
    "datasets": {},
    "timeout_seconds": 5,
    "memory_mb": 128,
    "max_output_bytes": 4096,
}
request = urllib.request.Request(
    "http://127.0.0.1:8090/v1/execute",
    data=json.dumps(body).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Sandbox-Token": os.environ["SANDBOX_TOKEN"],
    },
)
try:
    print(urllib.request.urlopen(request, timeout=15).read().decode())
except urllib.error.HTTPError as exc:
    print(exc.read().decode())
PY
```

预期：因 import 白名单或容器无网络返回失败，不应返回 `unexpected`。

确认一次性容器无遗留：

```bash
docker ps --filter label=share-data.sandbox=ephemeral
```

Expected: 请求结束后没有遗留容器。

确认 API 没有 Docker socket：

```bash
docker inspect share-data-app --format '{{json .Mounts}}'
```

Expected: 输出中没有 `/var/run/docker.sock`。

确认 Controller 没有发布宿主端口：

```bash
docker inspect share-data-sandbox-controller --format '{{json .NetworkSettings.Ports}}'
```

Expected: `{"8090/tcp":null}`。

确认 Compose token 插值不会接受空值：

```bash
SANDBOX_TOKEN= docker compose -f deploy/docker-compose.yml config
```

Expected: 命令失败并提示 `SANDBOX_TOKEN` 必须设置；不得用空 token 启动部署。

委员会 worker 默认由 `COMMITTEE_ENABLED=0` 关闭。启用时需在 `.env` 配置外部
Redis，部署编排不会创建内置 Redis。普通队列需要 Redis；LangGraph checkpoint
的首次 `setup()` 还要求 Redis Stack，或至少启用 RedisJSON 与 RediSearch。
checkpoint 初始化失败会返回不可用状态，不影响主应用启动。
若 Redis 可连接但缺少 RedisJSON/RediSearch，委员会健康接口会明确返回
`redis_stack_required`，worker 不会回退到进程内 checkpoint。
`committee-worker` 内置启用 RQ scheduler，用于执行自动恢复的延迟任务；
本 compose 与打包产物均通过同一 worker 模块启动，无需额外 scheduler 服务。

启用委员会的最小配置如下；尖括号内容必须由部署机密钥系统或本地 `.env`
替换，不得写进镜像、compose 或打包目录：

```dotenv
COMMITTEE_ENABLED=1
REDIS_HOST=<redis-host>
REDIS_PORT=6379
REDIS_PASSWORD=<redis-password>
REDIS_SSL=1
REDIS_DB=0
```

密码轮换顺序：先让 Redis 同时接受新旧凭据，在密钥系统或部署机 `.env`
写入新密码，依次执行 `docker compose restart committee-worker share-data`，
确认两个健康检查通过后撤销旧凭据。故障排查时只记录异常类型和脱敏端点，
不要输出 `.env`、连接 URL 或密码。
所有曾出现在源码、聊天、日志或旧发布包中的 Redis、Mongo、JWT 和 LLM
加密凭据必须立即轮换；新值应由密钥系统注入，不能复制到本目录。

## 架构说明

打包脚本默认构建 **`linux/amd64`**（适配绿联 NAS / 常见 Intel 服务器）。

若出现 `exec /usr/bin/sh: exec format error`，说明镜像架构与 NAS 不一致（例如在 Apple Silicon Mac 上打成了 arm64）。请重新打包：

```bash
DOCKER_MIRROR=docker.1ms.run PLATFORM=linux/amd64 ./scripts/package-docker.sh
```
