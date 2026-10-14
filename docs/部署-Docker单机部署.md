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

   `backend`、`frontend` 两个服务挂在 compose 的 **`deploy` profile** 下，所以：

   - 服务器上按上面这条走即可（`deploy.sh` 每条 compose 命令都自带 `--profile deploy`）；
   - 手工执行时务必带上 profile，否则只会拉起 postgres/neo4j：
     `docker compose --env-file backend/.env --profile deploy up -d`

5. 其它子命令：`bash deploy.sh check|status|logs [服务]|restart|down`。

## 升级（拉新代码后重新部署）

```bash
cd /opt/docgraph/app
git pull
bash deploy.sh                 # 校验 → dc build 重建 backend 镜像 → up -d → 健康检查
# 想直接占 80：FRONTEND_HOST_PORT=80 bash deploy.sh
```

- **必须 rebuild 才生效**：`deploy.sh` 会 `dc build`，改过的代码随镜像更新；`bash deploy.sh restart` 只是重启容器，不带新代码，别拿它当升级手段。
- **幂等、无数据风险**：可反复执行；`backend/.env` 与数据卷（`postgres_data` / `neo4j_data` / `uploads_data`）都不受影响。
- **无需手工迁移数据库或重灌图谱**：启动时的 `init_db()` 是幂等建表 + 增量 DDL；规则图谱按规则集快照存于 Neo4j，升级不改动。`requirements.txt` 未变时 pip 层命中缓存，不会重装依赖。
- **systemd + nginx 形态**（腾讯云那份指引）：`git pull` → `sudo systemctl restart docgraph-backend`；依赖有变化才重建 venv，前端有改动才 `npm ci && npm run build` 并重新拷贝 `dist`。
- **升级后自检**：`bash deploy.sh check` 通过，并在「系统设置 → 运行参数」能看到随版本新增的开关（例如 `LLM 全量复核（只提异议，不改判）`，默认关闭）。新增开关只写数据库 `system_settings`，不需要改 `.env` 或 compose。

### 升级前后的版本核对

版本号真相是仓库根 `VERSION` 文件，`deploy.sh` 会把它与 git 定位（提交 / tag / dirty）一起注入两个镜像
——容器里没有 `.git`，靠这一步镜像才报得出准确版本。因此：

- 部署结束会打印「运行版本：`{...}`」（来自 `GET /api/version`），一眼确认服务器这次跑的是哪个提交；
- 手动核对：`curl http://127.0.0.1:${FRONTEND_HOST_PORT}/api/version`；
- 与本机开发环境对比：本机执行 `powershell -File scripts\version.ps1 -Remote https://<域名>`，
  脚本列出「本机工作区 / 本机运行中后端 / 服务器正式环境」三行并按提交号给结论；
- 网页右上角也有版本徽标（悬停看前后端明细），前后端不是同一次构建时会标橙提示强刷。
- `deploy.sh` 在「工作树有未提交改动」或「`VERSION` 还没打对应 tag」时会告警（不阻断）。

字段含义、来源与发版流程见 [版本管理](版本管理.md)。

### 重启会回收"中断"的任务

OCR 与审查任务都跑在进程内后台线程，因此**容器/进程重启时，启动前遗留的 running 任务会被置为 failed（阶段显示「已中断」）**，需要重新发起：

- OCR：重启后不再永久显示"识别中"，失败的重新点识别即可；
- 审查：启动瞬间正在跑的那条审查会被标记中断，重跑一次审查即可；
- 该自愈逻辑假设**单副本、单 worker**（`backend/Dockerfile` 的 CMD 未使用 `--workers`，compose 也只有一份 `backend` 服务）。将来若扩容成多副本或多 worker，启动时的全量回收会误杀其它副本正在跑的任务，需改为只回收本进程创建的任务并辅以心跳。

## 关键约定

- **容器内一律用服务名**：`PG_HOST=postgres`、`PG_PORT=5432`、`NEO4J_URI=bolt://neo4j:7687`。`.env` 里给宿主机开发用的 `localhost:15432/17687` 会被 compose 的 `environment` 覆盖，不要改回来。
- **`deploy` profile**：`backend`/`frontend` 只在 `--profile deploy` 时参与编排。服务器部署走 `deploy.sh`（已自带）；本地裸 `docker compose up -d` 因此仍然只起数据库，和以前的手感一致。
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
| 界面右上角版本徽标显示「版本未知」 | 该镜像是直接 `docker compose --profile deploy build` 出来的（没有 build args），用 `bash deploy.sh` 重新构建即可带上版本；详见 [版本管理](版本管理.md)。 |

## 与 Windows 本地开发的关系

两套方式共用同一个 `docker-compose.yml`，靠 `deploy` profile 分开，互不干扰：

| 用途 | 命令 | 形态 |
| --- | --- | --- |
| 日常开发（主力） | `docker compose up -d postgres neo4j` + `start_services.bat` | 后端 `backend\.venv` 跑 uvicorn `--reload`（8800），前端 Vite dev server（5173），改代码即时生效 |
| 发布前预演 / 环境对齐 | `BACKEND_HOST_PORT=8811 docker compose --profile deploy up -d --build backend frontend` | 容器化形态（前端 8080、后端 8811），与服务器同一套镜像与 Linux 依赖 |
| 只收容器、留数据库 | `docker compose --profile deploy rm -sf backend frontend` | 容器删除，`postgres`/`neo4j` 与数据卷不受影响 |

注意：`deploy.sh` 用 `--env-file backend/.env` 解析数据库口令，裸 `docker compose` 则用内置默认值
（`postgres` / `neo4jpassword`），两套口令解析结果不同，会让 `postgres`/`neo4j` 容器被**重建一次**
（数据卷不动、数据不丢，但数据库会重启几十秒）。本机想避免这次重建，就把本地命令也写成
`docker compose --env-file backend/.env up -d postgres neo4j`。

两者同时跑要注意三点：

1. **端口**：容器后端默认要 `127.0.0.1:8800`，与 dev 后端冲突，并存时用 `BACKEND_HOST_PORT=8811`。
2. **上传件是两套存储**：dev 写 `backend/uploads/`，容器写 `uploads_data` 卷，数据库却是共享的，所以会出现"记录在、文件打不开"。要在本地共用，给 `backend` 服务加一行 bind mount `./backend/uploads:/data/uploads`。
3. **镜像里的代码是构建那一刻的**，改了代码不 rebuild 就不会生效，别拿 8080 上的旧构建当调试目标。

本地开发不会碰容器产物：`uploads/`、`backend/uploads/`、`frontend/dist/`、`node_modules/` 都在 `.gitignore` 内，镜像与卷只占 Docker 空间。

> 注意：本地 `backend/.env` 若已把数据库口令改成非默认值，而 postgres 数据卷是很早以前用默认
> 口令初始化的，两者不会自动同步（`POSTGRES_PASSWORD` 只在数据卷首次初始化时生效）。
> 换口令要连数据卷一起处理，否则先改 `.env` 只会得到 `password authentication failed`。
