#!/usr/bin/env python3
"""本地开发账号工具（仅限 APP_DEBUG=true 使用）。

背景：`python -m app.bootstrap` 是**一次性创建**入口 —— 账号已存在时它会校验密码，
密码不符直接报 ADMIN_PASSWORD_MISMATCH，因此无法用来找回/重置密码。
本脚本补上"查看账号"和"重置密码/解锁"两件事，供本地开发排障使用。

用法（在仓库根目录执行）：

    ./dev.sh admin list
    ./dev.sh admin set-password admin@example.com
    ./dev.sh admin set-password admin@example.com --password 'MyPassw0rd'
    ./dev.sh admin unlock admin@example.com

等价于：

    backend/.venv/bin/python scripts/dev_admin.py <同上子命令>
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND_DIR))
# backend/app/config.py 的 env_file=".env" 是相对工作目录解析的，
# 这里先切到 backend/，保证从仓库根或任意目录执行都能读到同一份配置。
os.chdir(_BACKEND_DIR)

from sqlalchemy import select  # noqa: E402

from app.auth.security import hash_password, validate_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Tenant, TenantMembership, UserAccount  # noqa: E402


def _require_debug() -> None:
    if not settings.app_debug:
        print(
            "拒绝执行：本工具仅用于本地开发（需 APP_DEBUG=true）。"
            "生产环境请走平台管理员的密码重置流程。",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        try:
            validate_password(value)
            return value
        except ValueError:
            continue


def _find_user(db, email: str) -> UserAccount | None:
    return db.execute(
        select(UserAccount).where(UserAccount.email_normalized == email.strip().lower())
    ).scalar_one_or_none()


def _memberships(db, user_id) -> list[tuple[str, str]]:
    rows = db.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user_id)
    ).all()
    return [(tenant.name, membership.role) for membership, tenant in rows]


def cmd_list(_args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        users = db.execute(select(UserAccount).order_by(UserAccount.created_at)).scalars().all()
        if not users:
            print("当前没有任何账号。创建租户管理员：")
            print("  cd backend && BOOTSTRAP_TENANT_ADMIN_EMAIL=admin@example.com \\")
            print("    BOOTSTRAP_TENANT_ADMIN_PHONE=13800138000 \\")
            print("    BOOTSTRAP_TENANT_ADMIN_PASSWORD='你自己的密码' \\")
            print("    .venv/bin/python -m app.bootstrap default-tenant-admin")
            return 1

        print(f"{'邮箱':<32} {'姓名':<16} {'角色':<14} {'状态':<8} 租户")
        print("-" * 100)
        for user in users:
            memberships = _memberships(db, user.id)
            role = "平台管理员" if user.is_platform_admin else (
                "、".join(sorted({r for _n, r in memberships})) or "-"
            )
            state = []
            if user.locked_until and user.locked_until > datetime.now(timezone.utc).replace(tzinfo=None):
                state.append("已锁定")
            if not user.email_verified_at:
                state.append("邮箱未验证")
            status = "、".join(state) or "正常"
            tenants = "、".join(n for n, _r in memberships) or "-"
            print(f"{user.email_normalized:<32} {user.name:<16} {role:<14} {status:<8} {tenants}")
        print("\n登录入口：http://localhost:5173（邮箱 + 密码）")
        return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    password = args.password or _generate_password()
    try:
        validate_password(password)
    except ValueError as exc:
        print(f"密码不符合策略：{exc}", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        user = _find_user(db, args.email)
        if user is None:
            print(f"未找到账号：{args.email}（可用 `./dev.sh admin list` 查看）", file=sys.stderr)
            return 1

        user.password_hash = hash_password(password)
        # 顺带解锁：连续失败锁定是本地开发最常见的"密码明明对却登不进"原因
        user.failed_login_count = 0
        user.locked_until = None
        db.commit()

        print(f"已重置密码：{user.email_normalized}（{user.name}）")
        print(f"新密码：{password}")
        print("登录入口：http://localhost:5173")
        if not user.email_verified_at:
            print("提示：该账号邮箱未验证，登录可能被拒；请用 app.bootstrap 重新初始化。")
        return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        user = _find_user(db, args.email)
        if user is None:
            print(f"未找到账号：{args.email}", file=sys.stderr)
            return 1
        user.failed_login_count = 0
        user.locked_until = None
        db.commit()
        print(f"已解锁：{user.email_normalized}（失败计数与锁定时间已清零，密码未改动）")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev_admin",
        description="DocGraph 本地开发账号工具（查看账号 / 重置密码 / 解锁）",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出全部账号及其角色、锁定状态")

    reset = sub.add_parser("set-password", help="重置指定邮箱的密码（并解锁）")
    reset.add_argument("email", help="账号邮箱，如 admin@example.com")
    reset.add_argument("--password", help="指定新密码（不填则随机生成并打印）")

    unlock = sub.add_parser("unlock", help="只清除登录失败锁定，不改密码")
    unlock.add_argument("email", help="账号邮箱")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2

    _require_debug()

    handlers = {"list": cmd_list, "set-password": cmd_set_password, "unlock": cmd_unlock}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
