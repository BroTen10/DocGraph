<p align="center">
  <img src="./docgraph-brand/docgraph-icon.svg" width="112" alt="DocGraph 智图审图标">
</p>

<h1 align="center">DocGraph · 智图审</h1>

<p align="center">
  <strong>把贸易单证审查，从人工翻找升级为可追溯的图谱推理。</strong>
</p>

<p align="center">
  上传一整套业务单证，自动完成 OCR 与字段抽取，再依据可配置的规则图谱进行跨文档核验。
</p>

<p align="center">
  <a href="#快速开始"><img src="https://img.shields.io/badge/快速开始-5_min-4F46E5?style=flat-square" alt="快速开始"></a>
  <img src="https://img.shields.io/badge/License-MIT-22C55E?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=0B1020" alt="React 18">
  <img src="https://img.shields.io/badge/FastAPI-REST-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
</p>

<p align="center">
  <a href="#项目亮点">项目亮点</a> ·
  <a href="#工作原理">工作原理</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#docker-部署">Docker 部署</a> ·
  <a href="#项目文档">项目文档</a>
</p>

> **项目状态：MVP。** 当前版本已包含邮箱注册审核、固定角色权限和多租户隔离。生产环境仍须配置 HTTPS、SMTP、强随机 `APP_SECRET_KEY`，并完成定期备份与安全监控。

## 项目亮点

| 亮点 | 与传统“逐份检查”相比，DocGraph 做了什么 |
| --- | --- |
| **规则即图谱** | 把单证、字段和业务约束建模成 Neo4j 图谱，由 `REQUIRED`、`MUST_STAMP`、`COMPARE_TO` 等关系驱动审查，而不是把业务规则硬编码在页面里。 |
| **跨单证核验** | 不只看一份文件是否“填对”，还检查发票、报关单、委托单、协议等材料之间的金额、日期、重量和齐套性。 |
| **结论可追溯** | 每条结果都回到具体规则、比对字段和源单证，便于审查员复核，而不是只给出一个无法解释的风险标签。 |
| **业务规则可配置** | 支持多规则集、文档类型管理和规则文档导入；不同贸易场景可以使用各自的规则、图谱和审查结果。 |
| **三态审查结果** | 区分 **通过 / 不通过 / 无法核验**。数据缺失时不会强行判错，帮助团队优先处理真正有把握命中问题的单据。 |
| **端到端可运行** | React 前端、FastAPI 后端、PostgreSQL、Neo4j、OCR 和 LLM 全部打通，同时提供本地开发与 Docker 单机部署方案。 |

## 工作原理

```mermaid
flowchart LR
    A[上传整套单证] --> B[OCR 与字段抽取]
    C[业务规则] --> D[规则图谱]
    D --> E[人工确认]
    B --> F[跨文档审查]
    E --> F
    F --> G{审查结论}
    G -->|通过| H[闭合结果]
    G -->|不通过| I[定位规则与证据]
    G -->|无法核验| J[标记缺失字段]
```

一个典型场景中，DocGraph 可以同时检查：

- 发票金额是否在约定容差内等于报关单总价；
- 委托单签订日期是否不晚于报关单申报日期；
- 当前业务批次是否缺少必备单证；
- 需要盖章的材料是否检测到印章。

## 功能地图

- **规则集管理**：按业务场景隔离规则、文档和审查结果。
- **文档类型**：维护文件分类、关键字段和别名。
- **规则导入**：支持从规则文档整理结构化检查项。
- **图谱确认**：将规则构建成图，并在审查前检查节点和关系。
- **批量识别**：处理 PDF、PNG、JPG/JPEG 和 DOCX。
- **审查执行**：并发处理 OCR 与审查任务，回收中断任务。
- **结果展示**：呈现三态结果、规则说明和原始单证证据。
- **版本核验**：对比前后端构建版本和运行提交，降低“部署了旧代码”的风险。
- **账号与权限**：邮箱注册、平台审核、手机号登记、服务端会话、租户管理员/审查员/只读角色。
- **租户隔离**：规则集、单证、任务、设置、上传文件和 Neo4j 图谱均按租户隔离。

## 系统架构

```mermaid
flowchart TB
    UI[React + Vite<br/>浏览器界面]
    API[FastAPI<br/>业务编排与审查引擎]
    PG[(PostgreSQL<br/>规则、文档与任务)]
    NEO[(Neo4j<br/>规则知识图谱)]
    FILES[(上传文件存储)]
    AI[OpenAI 兼容接口<br/>LLM / 多模态 OCR]

    UI -->|HTTP /api| API
    API --> PG
    API --> NEO
    API --> FILES
    API --> AI
```

| 模块 | 技术 |
| --- | --- |
| 前端 | React 18、TypeScript、Vite、Ant Design、D3.js |
| 后端 | Python 3.12、FastAPI、SQLAlchemy |
| 数据 | PostgreSQL 16、Neo4j 5.26 Community |
| 文档处理 | pdfplumber、PyMuPDF、Pillow、python-docx、RapidOCR |
| 智能服务 | 任意 OpenAI 兼容的 LLM 与多模态 OCR 接口 |
| 部署 | Docker Compose、nginx |

## 快速开始

### 环境要求

- Python 3.12
- Node.js 22 与 npm
- Docker Desktop 或 Docker Engine（支持 `docker compose`）
- 可用的 OpenAI 兼容 LLM/OCR 服务及 API Key

### 1. 克隆并配置

```powershell
git clone https://github.com/BroTen10/DocGraph.git
Set-Location DocGraph
Copy-Item backend\.env.example backend\.env
```

编辑 `backend\.env`，填写 `LLM_API_KEY`、SMTP 配置和 `APP_SECRET_KEY`。本地调试可临时使用
`EMAIL_BACKEND=console`；生产必须使用真实 SMTP。OCR 未单独配置时会复用 LLM Key。

### 2. 启动数据库

```powershell
docker compose --env-file backend\.env up -d postgres neo4j
```

### 3. 启动后端

```powershell
Set-Location backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

后端 API：<http://localhost:8800>  
本地 `APP_DEBUG=true` 时接口文档：<http://localhost:8800/docs>。生产默认关闭。

### 4. 启动前端

另开一个终端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

首次部署先初始化一个平台管理员（只执行一次）：

```powershell
Set-Location backend
$env:BOOTSTRAP_ADMIN_EMAIL="admin@example.com"
$env:BOOTSTRAP_ADMIN_PHONE="13800138000"
$env:BOOTSTRAP_ADMIN_PASSWORD="ReplaceWithAStrongPassword1"
.\.venv\Scripts\python.exe -m app.bootstrap
```

平台管理员登录后审核新组织注册申请；租户管理员登录后创建规则集、添加文档类型和审查规则。
正常入口为 <http://localhost:5173>。

从旧版单租户升级时，存量规则、合同、文档和图谱会迁入 `默认租户`。平台管理员按设计
不能读取租户业务数据，因此必须再显式初始化一个独立的默认租户管理员：

```powershell
$env:BOOTSTRAP_TENANT_ADMIN_EMAIL="legacy-admin@example.com"
$env:BOOTSTRAP_TENANT_ADMIN_PHONE="13900139000"
$env:BOOTSTRAP_TENANT_ADMIN_PASSWORD="ReplaceWithAStrongPassword1"
.\.venv\Scripts\python.exe -m app.bootstrap default-tenant-admin
```

该命令可安全重复执行：同一账号会恢复为有效租户管理员，不会自动把平台管理员提升为租户用户。

> 完成首次依赖安装后，也可以在仓库根目录运行 `start_services.bat` 同时启动前后端。

## Docker 部署

面向 Linux 单机环境：

```bash
git clone https://github.com/BroTen10/DocGraph.git
cd DocGraph
cp backend/.env.example backend/.env
vi backend/.env
bash deploy.sh
```

部署入口必须是已完成 TLS 的域名，并把 `PUBLIC_BASE_URL` 配成该 HTTPS 地址。部署脚本会构建镜像、
启动数据库、执行幂等安全迁移并完成健康检查。

```bash
bash deploy.sh check      # 部署后自检
bash deploy.sh status     # 查看容器状态
bash deploy.sh logs       # 查看日志
bash deploy.sh restart    # 重启服务
bash deploy.sh down       # 停止服务
```

部署成功后，管理员初始化必须在后端容器内执行。先按 `.env.example` 在
`backend/.env` 中填写对应的一次性 `BOOTSTRAP_*` 参数：

```bash
# 首次部署：平台管理员
docker compose --env-file backend/.env --profile deploy \
  exec backend python -m app.bootstrap

# 从旧版单租户升级：独立的默认租户管理员
docker compose --env-file backend/.env --profile deploy \
  exec backend python -m app.bootstrap default-tenant-admin
```

两组账号必须使用不同的邮箱和手机号。完成后清空六个 `BOOTSTRAP_*` 配置并重建后端：

```bash
docker compose --env-file backend/.env --profile deploy \
  up -d --force-recreate backend
```

完整说明见 [Docker 单机部署文档](docs/部署-Docker单机部署.md)。

## 常用配置

配置集中在 `backend\.env`：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | 空 | LLM/OCR 服务密钥，必填 |
| `APP_SECRET_KEY` | 空 | 至少 32 字符的随机密钥；生产缺少时拒绝启动 |
| `PUBLIC_BASE_URL` | `https://docs.example.com` | 邀请/验证/重置链接和 Origin 校验的对外地址 |
| `EMAIL_BACKEND` | `smtp` | 开发可用 `console`/`outbox`；生产只允许 `smtp` |
| `SMTP_HOST` / `MAIL_FROM` | 空 | 生产 SMTP 必填 |
| `SESSION_IDLE_MINUTES` | `120` | 会话空闲超时 |
| `SESSION_ABSOLUTE_HOURS` | `12` | 会话绝对有效期 |
| `LLM_BASE_URL` | OpenAI 兼容地址 | 模型接口 |
| `LLM_MODEL_NAME` | `deepseek-v4.1-flash` | 审查与抽取模型 |
| `UPLOAD_ROOT` | `./uploads` | 原始单证存储目录 |
| `MAX_FILE_SIZE_MB` | `50` | 单文件大小上限 |
| `AMOUNT_TOLERANCE_PERCENT` | `5` | 金额比较容差 |
| `WEIGHT_TOLERANCE_KG` | `0.5` | 重量比较容差 |
| `LLM_CONFIDENCE_THRESHOLD` | `0.9` | 规则转图谱自动确认阈值 |
| `DB_RESET_ON_STARTUP` | `false` | 是否清空并重建数据 |

> `DB_RESET_ON_STARTUP=true` 会删除业务数据并清空图谱，只能用于确认可以丢弃数据的隔离环境。

## 测试

先准备独立测试数据库并导出 `TEST_DATABASE_URL`，完整账号、权限与租户隔离验收：

```powershell
backend\.venv\Scripts\python.exe -m pytest tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

部分图谱测试需要 PostgreSQL 和 Neo4j 已启动：

```powershell
backend\.venv\Scripts\python.exe tests\run_graph_rule_tests.py
```

以下测试无需数据库或网络：

```powershell
backend\.venv\Scripts\python.exe tests\test_llm_client.py
backend\.venv\Scripts\python.exe tests\test_version.py
backend\.venv\Scripts\python.exe tests\test_rule_import_granularity.py
backend\.venv\Scripts\python.exe tests\test_missing_data_tristate.py
```

测试范围和扩展方式见 [tests/README.md](tests/README.md)。

## 目录结构

```text
DocGraph/
├── backend/                 FastAPI API、审查服务、OCR 与图谱逻辑
├── frontend/                React 管理界面
├── docs/                    架构、部署与版本管理文档
├── tests/                   图谱、规则导入、版本和三态结果测试
├── docgraph-brand/          项目图标与品牌素材
├── docker-compose.yml       本地数据库及生产服务编排
├── deploy.sh                Linux 单机部署脚本
├── LICENSE                  MIT License
└── THIRD-PARTY-NOTICES.md   第三方组件许可声明
```

## 安全提示

- 所有业务 API 都要求登录和服务端权限校验；仅健康检查、版本和公开认证接口豁免。
- 生产环境必须通过 HTTPS 入口访问，并正确配置 `PUBLIC_BASE_URL`；HSTS 应在 TLS 终结层启用。
- 首次平台管理员只能通过一次性 `python -m app.bootstrap` 创建；Docker 部署应在
  `backend` 容器内执行，禁止使用固定默认口令。
- 存量升级后必须在 `backend` 容器内执行
  `python -m app.bootstrap default-tenant-admin`，否则默认租户业务数据无人可访问。
- PostgreSQL、Neo4j 端口只绑定宿主机回环地址；生产脚本拒绝默认/示例数据库口令。
- `backend/.env`、上传的真实单证和数据库卷包含敏感信息，不应提交到 Git。
- 上线前应保存 PostgreSQL、Neo4j 和上传卷备份，并定期检查安全审计日志。

## 备份与升级

升级前先停止写入，备份文件必须保存在仓库和数据卷之外：

```bash
BACKUP_DIR="/opt/docgraph/backups/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$BACKUP_DIR"

docker compose --env-file backend/.env --profile deploy stop frontend backend
docker compose --env-file backend/.env exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$BACKUP_DIR/postgres.dump"
docker compose --env-file backend/.env --profile deploy stop postgres
```

Neo4j Community 的离线 dump 需要先停止 Neo4j，并把备份目录挂载进一次性容器：

```bash
docker compose --env-file backend/.env --profile deploy stop neo4j
install -d -m 700 "$BACKUP_DIR/neo4j"
sudo chown -R 7474:7474 "$BACKUP_DIR/neo4j"

docker compose --env-file backend/.env --profile deploy run \
  --rm --no-deps \
  -v "$BACKUP_DIR/neo4j:/backups" \
  --entrypoint neo4j-admin \
  neo4j database dump neo4j --to-path=/backups
```

上传卷使用只读挂载打包：

```bash
docker run --rm \
  -v docgraph_uploads_data:/from:ro \
  -v "$BACKUP_DIR:/to" \
  public.ecr.aws/docker/library/alpine:3.21 \
  sh -c 'tar -C /from -czf /to/uploads.tar.gz .'
```

同时保留 `backend/.env`、旧提交和旧镜像。随后执行 `bash deploy.sh`。启动过程会幂等添加
账号/租户字段、回填默认租户并校验约束。
禁止用 `DB_RESET_ON_STARTUP=true` 处理生产升级。

## 开源许可

DocGraph 仓库自身采用 [MIT License](LICENSE)，允许使用、修改、分发和商用，但分发时需保留原版权声明与许可声明。

第三方依赖遵循各自的许可证。需要特别评估的组件包括 PyMuPDF（AGPL-3.0 或商业授权）、Neo4j Community（GPL-3.0）和 psycopg2-binary（LGPL-3.0 及链接例外）。如计划分发或作为网络服务运营，请阅读 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

## 项目文档

- [Docker 单机部署](docs/部署-Docker单机部署.md)
- [登录权限与多租户隔离需求设计](docs/登录权限与多租户隔离-需求设计.md)
- [登录权限与多租户隔离验收记录](docs/登录权限与多租户隔离-验收记录.md)
- [版本管理](docs/版本管理.md)
- [规则解析控制架构设计](docs/规则解析控制架构设计.md)
- [泛化规则体系重构设计](docs/泛化规则体系重构设计.md)
- [第三方组件声明](THIRD-PARTY-NOTICES.md)

---

<p align="center">
  如果 DocGraph 的思路对你有帮助，欢迎 Star 或提交 Issue。
</p>
