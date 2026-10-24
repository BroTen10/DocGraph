# -*- coding: utf-8 -*-
"""OCR 中断任务回收（reconcile_stale_tasks）回归测试。

背景：OCR 跑在进程内后台线程，服务重启（含 debug reload）后任务行会永久停在
running，前端据此一直显示"识别中"并禁用触发按钮，用户表现为"任务一直执行、没有结果"。

本测试验证两个方向：
1. 早于本进程启动 / 超时的 running 任务被回收为 failed（并写明原因、补 end_time）；
2. 本进程启动之后创建的任务不被误杀。

运行：backend/.venv/Scripts/python.exe tests/test_ocr_task_reconcile.py
前置：PostgreSQL 在线（用 backend/.env 的 PG_* 配置）；会在 ocr_tasks 里创建并
      删除两条临时任务，不触碰其它数据。
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

from app.database import SessionLocal  # noqa: E402
from app.models import OcrTask, RuleSet  # noqa: E402
from app.services import ocr_task_service  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        _FAILED.append(name)


def main() -> None:
    db = SessionLocal()
    created: list[uuid.UUID] = []
    try:
        rule_set = db.execute(select(RuleSet).limit(1)).scalar()
        if rule_set is None:
            print("跳过：库中还没有规则集，无法挂载临时任务")
            return
        rule_set_id = rule_set.id
        tenant_id = rule_set.tenant_id

        now_ts = db.execute(select(func.now())).scalar()
        ocr_task_service.set_process_start(db)
        process_ts = ocr_task_service._PROCESS_STARTED_DB_TS
        check("能取到数据库时钟作为进程启动时刻", process_ts is not None, repr(process_ts))

        # 1) 陈旧任务：早于进程启动，且早已超时
        stale = OcrTask(
            tenant_id=tenant_id,
            rule_set_id=rule_set_id,
            scope="single_doc",
            status="running",
            stage="识别中: 陈旧任务.pdf",
            total_count=1,
            start_time=now_ts - timedelta(hours=3),
        )
        # 2) 新鲜任务：进程启动之后创建，不应被回收
        fresh = OcrTask(
            tenant_id=tenant_id,
            rule_set_id=rule_set_id,
            scope="single_doc",
            status="running",
            stage="识别中: 新任务.pdf",
            total_count=1,
            start_time=now_ts + timedelta(seconds=5),
        )
        db.add_all([stale, fresh])
        db.commit()
        created = [stale.id, fresh.id]

        swept = ocr_task_service.reconcile_stale_tasks(db, tenant_id=tenant_id)
        check("返回值只统计被回收的任务数", swept >= 1, f"swept={swept}")

        db.refresh(stale)
        db.refresh(fresh)
        check("陈旧 running 任务被置为 failed", stale.status == "failed", stale.status)
        check("回填写明中断原因", bool(stale.error) and "中断" in stale.error, str(stale.error))
        check("回填 end_time", stale.end_time is not None)
        check("未完成计数回填为失败", stale.failed_count == 1, str(stale.failed_count))
        check("进程启动后创建的任务不被误杀", fresh.status == "running", fresh.status)

        # 幂等：再来一次不应重复处理
        again = ocr_task_service.reconcile_stale_tasks(db, tenant_id=tenant_id)
        check("重复调用不再回收已完成回收的任务", again == 0, f"again={again}")
    finally:
        for tid in created:
            row = db.get(OcrTask, tid)
            if row is not None:
                db.delete(row)
        db.commit()
        print(f"（已清理 {len(created)} 条临时任务）")
        db.close()

    print()
    if _FAILED:
        print(f"FAILED {len(_FAILED)}: {', '.join(_FAILED)}")
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
