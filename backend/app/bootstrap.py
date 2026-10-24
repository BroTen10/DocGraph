"""One-time administrator provisioning.

Usage:

* ``python -m app.bootstrap`` or ``python -m app.bootstrap platform``
  reads ``BOOTSTRAP_ADMIN_EMAIL``, ``BOOTSTRAP_ADMIN_PHONE`` and
  ``BOOTSTRAP_ADMIN_PASSWORD``.
* ``python -m app.bootstrap default-tenant-admin`` reads
  ``BOOTSTRAP_TENANT_ADMIN_EMAIL``, ``BOOTSTRAP_TENANT_ADMIN_PHONE`` and
  ``BOOTSTRAP_TENANT_ADMIN_PASSWORD``. This is required once after an
  existing installation is migrated into the default tenant.
"""

from __future__ import annotations

import os
import sys


def _required_env(prefix: str) -> tuple[str, str, str]:
    email = os.environ.get(f"{prefix}_EMAIL", "").strip()
    phone = os.environ.get(f"{prefix}_PHONE", "").strip()
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    if not email or not phone or not password:
        print(
            f"缺少 {prefix}_EMAIL、{prefix}_PHONE 或 {prefix}_PASSWORD，拒绝运行",
            file=sys.stderr,
        )
        raise ValueError("missing_bootstrap_credentials")
    return email, phone, password


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv or [])
    mode = arguments[0] if arguments else "platform"
    if len(arguments) > 1 or mode not in {"platform", "default-tenant-admin"}:
        print(
            "用法：python -m app.bootstrap [platform|default-tenant-admin]",
            file=sys.stderr,
        )
        return 2

    try:
        if mode == "platform":
            email, phone, password = _required_env("BOOTSTRAP_ADMIN")
        else:
            email, phone, password = _required_env("BOOTSTRAP_TENANT_ADMIN")
    except ValueError as exc:
        if str(exc) == "missing_bootstrap_credentials":
            return 2
        raise

    from .database import init_db
    from .services.platform_service import (
        PlatformOperationError,
        create_default_tenant_admin,
        create_platform_admin,
    )

    init_db()
    try:
        if mode == "platform":
            create_platform_admin(email, phone, password)
            print("平台管理员已创建，请立即修改初始密码")
        else:
            create_default_tenant_admin(email, phone, password)
            print("默认租户管理员已创建或重新启用")
    except PlatformOperationError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
