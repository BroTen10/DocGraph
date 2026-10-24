# -*- coding: utf-8 -*-
"""审查任务中断回收（reconcile_stale_review_tasks）回归测试。

背景：审查管线与 OCR 一样跑在进程内后台线程，服务重启（含 debug reload）后任务行会
永久停在 running，任务列表一直显示"执行中"，人工无法判断是否失败。

本测试验证两个方向：
1. 早于本进程启动 / 超时的 running 任务被回收为 failed（写明原因、补 end_time）；
2. 本进程启动之后创建的任务不被误杀；重复调用幂等。

运行：backend/.venv/Scripts/python.exe tests/test_review_task_reconcile.py
前置：PostgreSQL 在线（用 backend/.env 的 PG_* 配置）；会在 review_tasks 里创建并删除
      两条临时任务，不触碰其它数据。
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import timedelta

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)
os.chdir(_BACKEND_DIR)  # Settings 的 env_file=".env" 按相对路径加载 backend/.env
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal, bind_tenant  # noqa: E402
from app.models import Contract, ReviewTask, RuleSet, Tenant  # noqa: E402
from app.tenant_scope import tenant_get  # noqa: E402
from app.services import review_service  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


def main() -> None:
    db = SessionLocal()
    created: list[uuid.UUID] = []
    rule_set_id = None
    tenant_id = None
    try:
        tenant = db.execute(
            select(Tenant).where(Tenant.code == "default")
        ).scalar_one()
        tenant_id = tenant.id
        bind_tenant(db, tenant_id)

        rule_set = RuleSet(
            tenant_id=tenant_id,
            name=f"review-reconcile-{uuid.uuid4().hex}",
            description="审查任务中断回收回归测试",
            doc_types=[],
            check_categories=[],
            is_default=False,
        )
        db.add(rule_set)
        db.flush()
        rule_set_id = rule_set.id
        contract = Contract(
            tenant_id=tenant_id,
            rule_set_id=rule_set.id,
            contract_no=f"RECONCILE-{uuid.uuid4().hex[:12]}",
            alias_list=[],
            status="uploaded",
            note="temporary reconciliation test",
        )
        db.add(contract)
        db.commit()
        contract_id = contract.id

        now_ts = db.execute(select(func.now())).scalar()
        review_service.set_process_start(db)
        process_ts = review_service._PROCESS_STARTED_DB_TS
        check("能取到数据库时钟作为进程启动时刻", process_ts is not None, repr(process_ts))

        # 1) 陈旧任务：早于进程启动，且早已超时
        stale = ReviewTask(
            tenant_id=tenant_id,
            contract_id=contract_id,
            status="running",
            progress=60,
            stage="规则比对中",
            summary={},
            start_time=now_ts - timedelta(hours=3),
        )
        # 2) 新鲜任务：进程启动之后创建，不应被回收
        fresh = ReviewTask(
            tenant_id=tenant_id,
            contract_id=contract_id,
            status="running",
            progress=10,
            stage="OCR 与字段提取中",
            summary={},
            start_time=now_ts + timedelta(seconds=5),
        )
        db.add_all([stale, fresh])
        db.commit()
        created = [stale.id, fresh.id]

        swept = review_service.reconcile_stale_review_tasks(
            db,
            tenant_id=tenant_id,
        )
        check("返回值只统计被回收的任务数", swept >= 1, f"swept={swept}")

        db.refresh(stale)
        db.refresh(fresh)
        check("陈旧 running 任务被置为 failed", stale.status == "failed", stale.status)
        check("阶段写为已中断", stale.stage == "已中断", str(stale.stage))
        check("回填写明中断原因", bool(stale.error) and "中断" in stale.error, str(stale.error))
        check("回填 end_time", stale.end_time is not None)
        check("进程启动后创建的任务不被误杀", fresh.status == "running", fresh.status)

        again = review_service.reconcile_stale_review_tasks(
            db,
            tenant_id=tenant_id,
        )
        check("重复调用不再回收已完成回收的任务", again == 0, f"again={again}")
    finally:
        db.rollback()
        if tenant_id is not None:
            for tid in created:
                row = tenant_get(db, ReviewTask, tid, tenant_id)
                if row is not None:
                    db.delete(row)
            db.commit()
            if rule_set_id is not None:
                rule_set = tenant_get(db, RuleSet, rule_set_id, tenant_id)
                if rule_set is not None:
                    db.delete(rule_set)
                    db.commit()
        print(f"（已清理 {len(created)} 条临时审查任务）")
        db.close()

    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


def test_review_task_reconcile(initialized_database) -> None:
    _FAILED.clear()
    main()


if __name__ == "__main__":
    main()
