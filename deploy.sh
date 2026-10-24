#!/usr/bin/env bash
# =============================================================================
# DocGraph 单机 Docker 部署脚本
#
#   bash deploy.sh              构建镜像 → 启动 → 健康检查（幂等，可重复执行）
#   bash deploy.sh check        只做健康检查
#   bash deploy.sh status       查看容器状态
#   bash deploy.sh logs [服务]  跟踪日志（postgres/neo4j/backend/frontend）
#   bash deploy.sh restart      重启容器（不重建镜像）
#   bash deploy.sh down         停止并移除容器与网络（保留数据卷）
#
# 版本号（对比本机开发环境 vs 服务器正式环境）：
#   版本号真相是仓库根 VERSION 文件，脚本把它与 git 定位（提交 / tag / dirty）
#   一起作为 build args 注入两个镜像 —— 容器里没有 .git，靠这一步才能报出准确版本。
#   部署结束会打印 /api/version，与本机后端 http://localhost:8800/api/version 对比即可。
#
# 端口（环境变量覆盖）：
#   FRONTEND_HOST_PORT=8080   前端/统一入口，对外
#   BACKEND_HOST_PORT=8800    后端，只绑 127.0.0.1，供本机排障
#   例如把前端放到 80：FRONTEND_HOST_PORT=80 bash deploy.sh
#
# backend/frontend 两个服务挂在 compose 的 deploy profile 下（裸
# `docker compose up -d` 只起 postgres/neo4j，本地开发不受影响），本脚本
# 的每条 compose 命令都会自动带上 --profile deploy。
#
# 前置条件：Docker Engine + compose v2；backend/.env（缺失会从 .env.example 生成，
# 生成后必须填 LLM_API_KEY 与 PG_PASSWORD）。
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

# 数据库口令只认 backend/.env 一份：--env-file 让 compose 的 ${PG_PASSWORD} /
# ${NEO4J_PASSWORD} 与后端进程读到同一个值。优先级 shell 环境 > --env-file > 根目录 .env。
COMPOSE_ENV_FILE="backend/.env"
COMPOSE_PROFILE="deploy"
dc() {
    if [ -f "$COMPOSE_ENV_FILE" ]; then
        docker compose --env-file "$COMPOSE_ENV_FILE" --profile "$COMPOSE_PROFILE" "$@"
    else
        docker compose --profile "$COMPOSE_PROFILE" "$@"
    fi
}

FRONTEND_HOST_PORT="${FRONTEND_HOST_PORT:-8080}"
BACKEND_HOST_PORT="${BACKEND_HOST_PORT:-8800}"
export FRONTEND_HOST_PORT BACKEND_HOST_PORT

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy][warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[deploy][fail]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
}

# ---- 版本信息：注入镜像，供 /api/version、启动日志与前端页脚使用 ----
# 版本号唯一真相是仓库根 VERSION；精确到提交的定位靠 git。容器里没有 .git，
# 所以在宿主机算好，经 compose 的 build.args 传进 backend / frontend 镜像。
collect_version_info() {
    if [ -f VERSION ]; then
        DOCGRAPH_APP_VERSION="$(tr -d ' \t\r\n' < VERSION)"
    else
        DOCGRAPH_APP_VERSION=""
        warn "仓库根缺少 VERSION 文件，镜像里的版本号会是 0.0.0"
    fi

    DOCGRAPH_GIT_COMMIT=""
    DOCGRAPH_GIT_DESCRIBE=""
    DOCGRAPH_GIT_TAG=""
    DOCGRAPH_GIT_BRANCH=""
    DOCGRAPH_GIT_DIRTY=""
    if command -v git >/dev/null 2>&1 && [ -d .git ]; then
        DOCGRAPH_GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || true)"
        DOCGRAPH_GIT_DESCRIBE="$(git describe --tags --dirty --always 2>/dev/null || true)"
        DOCGRAPH_GIT_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
        DOCGRAPH_GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
        if [ -n "$(git status --porcelain 2>/dev/null | head -n 1)" ]; then
            DOCGRAPH_GIT_DIRTY="true"
        else
            DOCGRAPH_GIT_DIRTY="false"
        fi
    elif [ -d .git ]; then
        warn "有 .git 但 git 命令不在 PATH：镜像只能报出版本号，报不出提交定位"
    else
        warn "当前目录不是 git 工作区：镜像只能报出版本号，报不出提交定位"
    fi

    DOCGRAPH_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    DOCGRAPH_BUILD_ENV="prod"

    export DOCGRAPH_APP_VERSION DOCGRAPH_GIT_COMMIT DOCGRAPH_GIT_DESCRIBE \
           DOCGRAPH_GIT_TAG DOCGRAPH_GIT_BRANCH DOCGRAPH_GIT_DIRTY \
           DOCGRAPH_BUILD_TIME DOCGRAPH_BUILD_ENV
}

# 读 backend/.env 里的键值（去掉空白、引号、CR）
read_env() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" backend/.env 2>/dev/null \
        | tail -n 1 | tr -d '\r' \
        | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

need_docker() {
    command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Engine"
    docker compose version >/dev/null 2>&1 || die "未找到 docker compose（需要 Compose v2 插件）"
    docker info >/dev/null 2>&1 || die "Docker 守护进程不可用：未启动，或当前用户不在 docker 组"
}

ensure_env_file() {
    [ -f backend/.env ] && return 0
    [ -f backend/.env.example ] || die "backend/.env 与 backend/.env.example 都不存在，无法继续"
    cp backend/.env.example backend/.env
    warn "backend/.env 不存在，已从 .env.example 生成"
    warn "请填好 LLM_API_KEY 与 PG_PASSWORD（须与 compose 的 postgres 口令一致）后重跑"
}

port_in_use() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$1" 2>/dev/null | tail -n +2 | grep -q . && return 0
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$" && return 0
    fi
    return 1
}

preflight() {
    ensure_env_file
    collect_version_info

    log "版本：${DOCGRAPH_APP_VERSION:-未知}+${DOCGRAPH_GIT_COMMIT:-无提交}（${DOCGRAPH_GIT_DESCRIBE:-无 git 信息}）"
    if [ "$DOCGRAPH_GIT_DIRTY" = "true" ]; then
        warn "工作树有未提交改动：镜像会带 dirty 标记，正式发布请先提交再部署"
    fi
    if [ -n "$DOCGRAPH_APP_VERSION" ] && command -v git >/dev/null 2>&1 && [ -d .git ] \
        && ! git rev-parse -q --verify "refs/tags/v${DOCGRAPH_APP_VERSION}" >/dev/null 2>&1; then
        warn "VERSION=${DOCGRAPH_APP_VERSION} 还没有对应的 tag v${DOCGRAPH_APP_VERSION}（最近 tag：${DOCGRAPH_GIT_TAG:-无}）"
    fi

    local reset
    reset="$(read_env DB_RESET_ON_STARTUP | tr 'A-Z' 'a-z')"
    [ "$reset" = "true" ] && die "backend/.env 里 DB_RESET_ON_STARTUP=true：启动会 DROP SCHEMA 并清空 Neo4j 图谱，部署环境请改为 false"

    case "${FRONTEND_HOST_PORT}${BACKEND_HOST_PORT}" in
        *[!0-9]*) die "端口必须是数字：FRONTEND_HOST_PORT=${FRONTEND_HOST_PORT} BACKEND_HOST_PORT=${BACKEND_HOST_PORT}" ;;
    esac

    local key
    key="$(read_env PG_PASSWORD)"
    case "$key" in
        ""|postgres|CHANGE_ME_DB_PASSWORD)
            die "backend/.env 的 PG_PASSWORD 为空、默认值或示例值，生产环境必须使用强随机口令"
            ;;
    esac
    key="$(read_env NEO4J_PASSWORD)"
    case "$key" in
        ""|neo4jpassword|CHANGE_ME_NEO4J_PASSWORD)
            die "backend/.env 的 NEO4J_PASSWORD 为空、默认值或示例值，生产环境必须使用强随机口令"
            ;;
    esac
    key="$(read_env APP_SECRET_KEY)"
    [ "${#key}" -ge 32 ] || die "backend/.env 的 APP_SECRET_KEY 少于 32 个字符，生产环境必须使用强随机值"
    case "$key" in
        change-me*|changeme*|replace-me*|your-secret-key*)
            die "backend/.env 的 APP_SECRET_KEY 仍是示例值，请生成至少 32 字节的随机密钥"
            ;;
    esac

    local public_url email_backend smtp_host mail_from
    public_url="$(read_env PUBLIC_BASE_URL)"
    case "$public_url" in
        https://*) ;;
        *) die "PUBLIC_BASE_URL 必须是 HTTPS 地址，例如 https://docs.example.com" ;;
    esac
    case "$public_url" in
        *example.com*) die "PUBLIC_BASE_URL 仍是示例域名，请改成真实 HTTPS 地址" ;;
    esac
    email_backend="$(read_env EMAIL_BACKEND)"
    [ -n "$email_backend" ] || email_backend="smtp"
    [ "$email_backend" = "smtp" ] || die "生产环境 EMAIL_BACKEND 必须为 smtp"
    smtp_host="$(read_env SMTP_HOST)"
    mail_from="$(read_env MAIL_FROM)"
    [ -n "$smtp_host" ] || die "EMAIL_BACKEND=smtp 时必须配置 SMTP_HOST"
    [ -n "$mail_from" ] || die "EMAIL_BACKEND=smtp 时必须配置 MAIL_FROM"
    [ "$smtp_host" != "smtp.example.com" ] || die "SMTP_HOST 仍是示例值"
    [ "$mail_from" != "no-reply@example.com" ] || die "MAIL_FROM 仍是示例值"

    key="$(read_env LLM_API_KEY)"
    case "$key" in
        ""|sk-your-*)
            warn "backend/.env 的 LLM_API_KEY 仍是占位值，规则解析与审查链路会调用失败"
            ;;
    esac

    if [ -n "${PG_PASSWORD:-}" ] || [ -n "${NEO4J_PASSWORD:-}" ]; then
        warn "shell 环境里设置了 PG_PASSWORD / NEO4J_PASSWORD，它会覆盖 backend/.env 里的值"
    fi

    if port_in_use "$FRONTEND_HOST_PORT" \
        && ! docker ps --format '{{.Ports}}' | grep -q ":${FRONTEND_HOST_PORT}->"; then
        warn "宿主机 ${FRONTEND_HOST_PORT} 已被其它进程占用，compose 启动会失败；"
        warn "换个端口即可，例如：FRONTEND_HOST_PORT=8090 bash deploy.sh"
    fi

    log "部署参数：前端 0.0.0.0:${FRONTEND_HOST_PORT} → 容器 80；后端 127.0.0.1:${BACKEND_HOST_PORT} → 容器 8800"
}

http_get() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 8 "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- --timeout=8 "$1"
    else
        die "宿主机既没有 curl 也没有 wget，无法做健康检查"
    fi
}

http_status() {
    if command -v curl >/dev/null 2>&1; then
        curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$1"
    else
        die "部署自检需要 curl 来校验受保护接口状态"
    fi
}

wait_health() {
    local url="http://127.0.0.1:${FRONTEND_HOST_PORT}/api/health" body i
    log "健康检查：${url}（最多等 180s）"
    for i in $(seq 1 60); do
        if body="$(http_get "$url" 2>/dev/null)"; then
            log "健康检查通过：${body}"
            return 0
        fi
        sleep 3
    done
    dc ps || true
    dc logs --tail=40 backend || true
    die "健康检查超时：${url} 未返回 200"
}

verify() {
    wait_health
    local base="http://127.0.0.1:${FRONTEND_HOST_PORT}" p

    local vjson
    if vjson="$(http_get "${base}/api/version" 2>/dev/null)"; then
        log "运行版本：${vjson}"
    else
        warn "/api/version 不可用（多半是旧镜像），本次部署的版本号是 ${DOCGRAPH_APP_VERSION:-未知}"
    fi

    log "接口探测（受保护接口必须返回 401）："
    for p in /api/constants/doc-types /api/rule-sets; do
        status="$(http_status "${base}${p}")"
        if [ "$status" = "401" ]; then
            printf '  [OK]   %s%s -> 401\n' "$base" "$p"
        else
            printf '  [FAIL] %s%s -> %s（预期 401）\n' "$base" "$p" "$status"
            die "受保护接口认证边界不符合预期"
        fi
    done
    if http_get "${base}/" >/dev/null 2>&1; then
        printf '  [OK]   %s/ （前端首页）\n' "$base"
    else
        die "前端首页不可用"
    fi
}

case "${1:-deploy}" in
    deploy)
        need_docker
        preflight
        log "构建镜像（首次约 3-8 分钟，含基础镜像与依赖下载）"
        dc build
        log "启动服务"
        dc up -d --remove-orphans
        dc ps
        verify
        echo
        log "完成：前端入口 http://<服务器IP>:${FRONTEND_HOST_PORT}"
        log "排障：bash deploy.sh logs backend / bash deploy.sh status / bash deploy.sh down"
        ;;
    check)
        need_docker
        verify
        ;;
    status)
        need_docker
        dc ps
        ;;
    logs)
        need_docker
        if [ -n "${2:-}" ]; then
            dc logs -f --tail=100 "$2"
        else
            dc logs -f --tail=100
        fi
        ;;
    restart)
        need_docker
        dc restart
        dc ps
        ;;
    down)
        need_docker
        dc down
        log "容器与网络已移除；数据卷 postgres_data / neo4j_data / neo4j_logs / uploads_data 保留"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        die "未知子命令：$1（可用：deploy | check | status | logs | restart | down）"
        ;;
esac
