#!/usr/bin/env bash
# 双击本文件即可启动 DocGraph 本地开发环境（macOS 会在终端中执行）。
# 命令行等价用法：./dev.sh start   /   ./启动开发环境.command
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
./dev.sh start
status=$?
echo
if [ -t 0 ]; then
  read -r -p "按回车键关闭此窗口（服务继续在后台运行）… " _
fi
exit "${status}"
