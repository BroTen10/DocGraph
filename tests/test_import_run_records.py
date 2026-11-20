# -*- coding: utf-8 -*-
"""规则导入运行记录（持久留档）测试。

背景：导入进度与解析错误此前只在进程内存，重启即丢，"LLM 判不了的部分"无人可见，
闭环里"人工只修 LLM 判不了的"就缺了入口。本测试验证：

1. 任务快照可落档、按状态变化更新、终态写 finished_at；
2. 解析 errors 与 warnings 被留档（人工据此修正）；
3. 记录按规则集隔离、按时间倒序列出；
4. 落档失败不影响导入（非法 rule_set_id 只记日志）；
5. 路由层 GET /api/rules/import-runs 可查（需 rules.view）。

运行：需 Postgres（TEST_DATABASE_URL 指向 *_test 库）：pytest tests/test_import_run_records.py
"""

from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

from sqlalchemy import select

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
sys.path.insert(0, _BACKEND_DIR)

from conftest import authenticate  # noqa: E402
from app.models import RuleImportRun, RuleSet  # noqa: E402
from app.services import rule_import_run_service as svc  # noqa: E402
from app.services import rule_import_task  # noqa: E402


def _task(tenant_id, rule_set_id, **kwargs) -> SimpleNamespace:
    base = dict(
        task_id=uuid.uuid4().hex,
        tenant_id=str(tenant_id),
        rule_set_id=str(rule_set_id),
        user_id=None,
        file_name="规则清单.xlsx",
        status="pending",
        message="",
        total_chunks=0,
        parsed_chunks=0,
        total_rules=0,
        imported_rules=0,
        import_errors=0,
        conflict_found=0,
        result=None,
        error=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _make_rule_set(db, tenant_id) -> RuleSet:
    rs = RuleSet(tenant_id=tenant_id, name=f"导入留档-{uuid.uuid4().hex[:6]}")
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return rs


def test_persist_creates_and_updates_run(db, default_tenant_id) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    task = _task(default_tenant_id, rs.id, status="parsing", total_chunks=3, parsed_chunks=1)

    svc.persist_task(task)
    rows = svc.list_runs(db, default_tenant_id, rs.id)
    assert len(rows) == 1 and rows[0].status == "parsing" and rows[0].total_chunks == 3

    task.status = "done"
    task.imported_rules = 5
    task.total_rules = 6
    task.result = {
        "errors": ["第 2 条：规则文本与结构化断言均为空，跳过"],
        "import_warnings": ["已跳过非规则工作表：填写说明"],
    }
    svc.persist_task(task)
    # 落档走独立 session：清掉本会话的身份映射缓存再读，避免读到旧快照
    db.expire_all()

    rows = svc.list_runs(db, default_tenant_id, rs.id)
    assert len(rows) == 1  # 同一 task_id 更新而不是新增
    run = rows[0]
    assert run.status == "done" and run.finished_at is not None
    assert run.imported_rules == 5 and run.import_errors == 1
    assert run.errors[0]["reason"].startswith("第 2 条")
    assert run.warnings == ["已跳过非规则工作表：填写说明"]


def test_task_level_error_is_recorded(db, default_tenant_id) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    task = _task(
        default_tenant_id,
        rs.id,
        status="error",
        error="文件内容为空，无法提取规则文本",
    )
    svc.persist_task(task)

    run = svc.list_runs(db, default_tenant_id, rs.id)[0]
    assert run.status == "error" and "文件内容为空" in (run.error or "")
    assert run.finished_at is not None


def test_runs_are_listed_by_time_desc(db, default_tenant_id) -> None:
    rs = _make_rule_set(db, default_tenant_id)
    for idx in range(3):
        svc.persist_task(_task(default_tenant_id, rs.id, status="done", imported_rules=idx))

    rows = svc.list_runs(db, default_tenant_id, rs.id, limit=2)
    assert len(rows) == 2
    assert {row.imported_rules for row in rows} == {1, 2}  # 最新的两条


def test_persist_failure_does_not_raise(db, default_tenant_id) -> None:
    """非法 rule_set_id（例如任务携带脏数据）只记日志，不抛异常、不写脏行。"""
    task = _task(default_tenant_id, "not-a-uuid", status="done")
    svc.persist_task(task)  # 不应抛异常
    assert db.execute(select(RuleImportRun)).scalars().all() == []


def test_route_lists_import_runs(client, admin_auth, db) -> None:
    rs = _make_rule_set(db, admin_auth.tenant_id)
    svc.persist_task(
        _task(
            admin_auth.tenant_id,
            rs.id,
            status="done",
            imported_rules=4,
            result={"errors": ["第 1 条：非合法对象，跳过"]},
        )
    )
    headers = authenticate(client, admin_auth)

    resp = client.get(
        "/api/rules/import-runs", params={"rule_set_id": str(rs.id)}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "done"
    assert body[0]["imported_rules"] == 4
    assert body[0]["errors"][0]["reason"].startswith("第 1 条")


def test_task_lifecycle_persists_automatically(db, default_tenant_id) -> None:
    """真实钩子路径：create_task/update_task 自动落档，进度类更新不写库。"""
    rs = _make_rule_set(db, default_tenant_id)
    task = rule_import_task.create_task(
        str(rs.id), default_tenant_id, uuid.uuid4(), "规则清单.xlsx"
    )
    db.expire_all()
    rows = svc.list_runs(db, default_tenant_id, rs.id)
    assert len(rows) == 1 and rows[0].status == "pending"

    rule_import_task.update_task(task, status="parsing", message="正在解析")
    db.expire_all()
    assert svc.list_runs(db, default_tenant_id, rs.id)[0].status == "parsing"

    # 进度类更新（状态未变）只改内存：不产生新的 DB 写入
    rule_import_task.update_task(task, parsed_chunks=2)
    db.expire_all()
    assert svc.list_runs(db, default_tenant_id, rs.id)[0].parsed_chunks == 0

    rule_import_task.update_task(
        task,
        status="done",
        imported_rules=3,
        result={"errors": ["第 1 条：非合法对象，跳过"]},
    )
    db.expire_all()
    run = svc.list_runs(db, default_tenant_id, rs.id)[0]
    assert run.status == "done" and run.imported_rules == 3
    assert run.errors[0]["reason"].startswith("第 1 条")
