"""One-time platform administrator provisioning.

Usage: ``python -m app.bootstrap`` with ``BOOTSTRAP_ADMIN_EMAIL`` and
``BOOTSTRAP_ADMIN_PASSWORD`` set. The command refuses to run without both.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password:
        print(
            "缺少 BOOTSTRAP_ADMIN_EMAIL 或 BOOTSTRAP_ADMIN_PASSWORD，拒绝运行",
            file=sys.stderr,
        )
        return 2

    from .database import init_db
    from .services.platform_service import PlatformOperationError, create_platform_admin

    init_db()
    try:
        create_platform_admin(email, password)
    except PlatformOperationError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print("平台管理员已创建，请立即修改初始密码")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
