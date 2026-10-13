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
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
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

    local reset
    reset="$(read_env DB_RESET_ON_STARTUP | tr 'A-Z' 'a-z')"
    [ "$reset" = "true" ] && die "backend/.env 里 DB_RESET_ON_STARTUP=true：启动会 DROP SCHEMA 并清空 Neo4j 图谱，部署环境请改为 false"

    case "${FRONTEND_HOST_PORT}${BACKEND_HOST_PORT}" in
        *[!0-9]*) die "端口必须是数字：FRONTEND_HOST_PORT=${FRONTEND_HOST_PORT} BACKEND_HOST_PORT=${BACKEND_HOST_PORT}" ;;
    esac

    local key
    key="$(read_env PG_PASSWORD)"
    [ -n "$key" ] || warn "backend/.env 未设置 PG_PASSWORD，将使用 compose 默认口令 postgres"
    key="$(read_env LLM_API_KEY)"
    if [ -z "$key" ] || [ "$key" = "sk-your-deepseek-key" ]; then
        warn "backend/.env 的 LLM_API_KEY 仍是占位值，规则解析与审查链路会调用失败"
    fi

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
    log "接口探测："
    for p in /api/constants/doc-types /api/rule-sets; do
        if http_get "${base}${p}" >/dev/null 2>&1; then
            printf '  [OK]   %s%s\n' "$base" "$p"
        else
            printf '  [FAIL] %s%s\n' "$base" "$p"
        fi
    done
    if http_get "${base}/" >/dev/null 2>&1; then
        printf '  [OK]   %s/ （前端首页）\n' "$base"
    else
        printf '  [FAIL] %s/ （前端首页）\n' "$base"
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
