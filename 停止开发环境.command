#!/usr/bin/env bash
# 双击本文件即可停止 DocGraph 本地开发环境（数据库容器保持运行）。
# 命令行等价用法：./dev.sh stop   /   ./停止开发环境.command
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
./dev.sh stop
status=$?
echo
if [ -t 0 ]; then
  read -r -p "按回车键关闭此窗口… " _
fi
exit "${status}"
