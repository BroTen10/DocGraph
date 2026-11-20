#!/usr/bin/env bash
# macOS / Linux 版 stop_services（Windows 请用 stop_services.bat）
# 等价于：./dev.sh stop（加 --with-deps 可连数据库容器一起停）
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev.sh" stop "$@"
