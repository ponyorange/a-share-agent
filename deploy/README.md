# Share Data Docker 部署

本目录产物由 `scripts/package-docker.sh` 打包到仓库根目录 `dist/`。

## 产物说明

| 文件 | 说明 |
|------|------|
| `share-data-<tag>.tar.gz` | `docker save` 导出的应用镜像 |
| `docker-compose.yml` | 编排文件（`pull_policy: never`，用本地镜像） |
| `.env.example` | 秘钥 / Mongo 等环境变量模板 |
| `IMAGE_TAG.txt` | 本次打包的镜像标签 |
| `load-and-run.sh` | 加载镜像并 `compose up` |
| `README.md` | 本说明 |

## 部署步骤

```bash
# 1. 加载镜像
gunzip -c share-data-*.tar.gz | docker load

# 2. 配置秘钥（不进镜像）
cp .env.example .env
# 编辑 MONGODB_URI / JWT_SECRET 等

# 3. 启动
docker compose up -d

# 4. 检查旧应用健康
curl -s http://127.0.0.1:8000/api/health

# 5. 启用委员会后，用登录令牌检查 Redis 与 checkpoint
curl -s -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8000/api/advisor/committee/health
```

或直接：`./load-and-run.sh`

生产必须保持 `APP_ENV=production` 与 `DEV_SEED_ENABLED=0`。
`MONGODB_URI`、`JWT_SECRET`、`LLM_ENCRYPTION_KEY` 均由部署机密钥系统注入；
后两者必须是相互独立、至少 32 字节的高熵值。LLM 密钥轮换窗口可设置
`LLM_ENCRYPTION_KEY_PREVIOUS`，完成数据重加密后立即删除旧值。

## 访问

| 地址 | 说明 |
|------|------|
| `http://<host>:8000/` | 交易顾问 |
| `http://<host>:8000/explorer/` | 数据后台 |
| `http://<host>:8000/api/health` | 健康检查 |

MongoDB 需自行准备；通过 `.env` 中的 `MONGODB_URI` 连接。

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
