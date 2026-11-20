#!/usr/bin/env bash
# macOS / Linux 版 start_services（Windows 请用 start_services.bat）
# 等价于：./dev.sh start
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev.sh" start "$@"
