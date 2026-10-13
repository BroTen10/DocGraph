# DocGraph 单机 Docker 部署

面向 Linux 服务器的一键部署。数据库、后端、前端全部容器化，前端 nginx 同时托管静态产物并反代 `/api`。

## 组成

| 服务 | 镜像 / 构建 | 宿主端口 | 说明 |
| --- | --- | --- | --- |
| `frontend` | `frontend/Dockerfile`（node 构建 → nginx:1.27-alpine） | `${FRONTEND_HOST_PORT:-8080}` | 统一对外入口，托管 SPA 并反代 `/api` 到 backend |
| `backend` | `backend/Dockerfile`（python:3.12-slim + uvicorn） | `127.0.0.1:${BACKEND_HOST_PORT:-8800}` | 只绑回环，供本机排障；API 无鉴权，不直接对公网暴露 |
| `postgres` | `public.ecr.aws/docker/library/postgres:16-alpine` | `15432` | 卷 `postgres_data` |
| `neo4j` | `public.ecr.aws/docker/library/neo4j:5.26.0-community` | `7474` / `17687` | 卷 `neo4j_data`、`neo4j_logs` |
| 上传文件 | 卷 `uploads_data` → 容器 `/data/uploads` | — | `UPLOAD_ROOT` 由 compose 注入 |

## 步骤

1. 装 Docker Engine 与 compose v2（`docker compose version` 能打印版本即可）。
2. 把仓库放到服务器，例如 `/opt/docgraph/app`。
3. 准备配置：

   ```bash
   cp backend/.env.example backend/.env
   vi backend/.env          # 至少填 LLM_API_KEY；PG_PASSWORD 要与 compose 的 postgres 口令一致
   ```

   `backend/.env` 不在 git 里，属于机密文件，别提交、别打进镜像。

   **数据库口令只有一份来源**：`deploy.sh` 用 `docker compose --env-file backend/.env` 启动，
   所以 compose 里的 `POSTGRES_PASSWORD`、`NEO4J_AUTH` 与后端进程读到的 `PG_PASSWORD`、
   `NEO4J_PASSWORD` 一定同值。注意 compose 变量优先级是 **shell 环境 > `--env-file` > 根目录 `.env`**，
   如果 shell 里 export 过同名变量，会覆盖 `backend/.env`（脚本会提示）。
   手工敲 `docker compose up -d` 而不带 `--env-file` 时，会用根目录 `.env` 或内置默认值
   （`postgres` / `neo4jpassword`）初始化数据库，后端随即认证失败 —— 请统一走 `bash deploy.sh`。

4. 执行部署（幂等，可反复跑）：

   ```bash
   cd /opt/docgraph/app
   bash deploy.sh                      # 默认 8080 对外
   FRONTEND_HOST_PORT=80 bash deploy.sh   # 想直接占 80
   ```

   脚本会：校验 Docker → 检查 `.env` → 拦截 `DB_RESET_ON_STARTUP=true` → 构建 → 启动 → 轮询 `/api/health` → 探测 `/api/`、`/api/constants/doc-types`、`/api/rule-sets`。

5. 其它子命令：`bash deploy.sh check|status|logs [服务]|restart|down`。

## 关键约定

- **容器内一律用服务名**：`PG_HOST=postgres`、`PG_PORT=5432`、`NEO4J_URI=bolt://neo4j:7687`。`.env` 里给宿主机开发用的 `localhost:15432/17687` 会被 compose 的 `environment` 覆盖，不要改回来。
- **`DB_RESET_ON_STARTUP` 必须为 false**：true 会 `DROP SCHEMA` 并清空 Neo4j 图谱，脚本会直接拒绝启动。
- **上传文件在卷里**：老数据在开发机的 `backend/uploads/`，迁移用
  `docker compose cp backend/uploads/. backend:/data/uploads/`。
- **备份**：`docker compose exec postgres pg_dump -U postgres doc_review > backup.sql`，Neo4j 与上传卷直接打包对应卷目录。
- **域名 / HTTPS**：容器只提供 HTTP。要么让宿主机 nginx 反代 `127.0.0.1:${FRONTEND_HOST_PORT}` 再用 certbot 签证书，要么把 `FRONTEND_HOST_PORT` 设成 `80` 并另配 TLS 终止。

## 常见问题

| 症状 | 原因与处理 |
| --- | --- |
| 拉基础镜像超时（`registry-1.docker.io`） | 国内网络常无法直连 Docker Hub。本仓库基础镜像已统一用 `public.ecr.aws`；如仍慢，配置 registry mirror 或改 `Dockerfile` 里的 `FROM`。 |
| `apt-get` 卡住 | 默认 `deb.debian.org` 在部分网络下不可达，`backend/Dockerfile` 已默认替换为清华源，可用 `--build-arg APT_MIRROR=` 覆盖。 |
| OCR 报 `ImportError: libxcb.so.1` | 缺系统库。`backend/Dockerfile` 已装 `libgl1 libglib2.0-0 libxcb1`，自定义镜像时别删。 |
| 大文件上传返回 413 | nginx `client_max_body_size` 需大于后端 `MAX_FILE_SIZE_MB`，当前 60m。 |
| 部署脚本报 `$'\r': command not found` | 脚本被 CRLF 污染，`.gitattributes` 已固定 `*.sh` 为 LF；若是拷贝目录导致的，执行 `sed -i 's/\r$//' deploy.sh`。 |
| 前端页面能开、接口 404 | 检查 nginx 是否把 `/api/` 代理到 `backend:8800`，以及 backend 容器是否 healthy。 |

## 与 Windows 本地开发的关系

本地开发仍用 `start_services.bat` / `stop_services.bat`（后端 `.venv` + Vite dev server）。两套方式共用同一个 `docker-compose.yml`：`docker compose up -d postgres neo4j` 只起数据库即可继续本地开发。

> 注意：本地 `backend/.env` 若已把数据库口令改成非默认值，而 postgres 数据卷是很早以前用默认
> 口令初始化的，两者不会自动同步（`POSTGRES_PASSWORD` 只在数据卷首次初始化时生效）。
> 换口令要连数据卷一起处理，否则先改 `.env` 只会得到 `password authentication failed`。
