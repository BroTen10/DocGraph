# -*- coding: utf-8 -*-
"""端到端验证：人工修正回灌是否真的改变了 LLM 的规则解析（需要 LLM）。

验证对象是基线的核心承诺：**同一类修正不需要出现第二次**。
做法是同一段规则文本的 A/B 对照（除"账本里有没有人工修正"外，其余输入完全相同）：

  1. 第一次解析 → 记录模型自然给出的 check_category（C_model）；
  2. 模拟人工修正：把该类规则的 check_category 改成另一个类别（C_human，≠ C_model），
     写进修正账本（真实场景由 PUT /api/rules/{id} 自动记账）；
  3. 第二次解析同一段文本 → 断言模型给出的类别变成 C_human；
  4. 清理临时规则集与账本行。

只有第 3 步成立，"人改一次、模型学会"才算真的闭环。

运行（会调用 LLM，产生少量 token 费用）：
    cd backend && .venv/bin/python ../tests/rule_loop_live.py
前置：Postgres 已启动（backend/.env），LLM_* 已配置。脚本自建临时规则集，跑完删除。
"""

from __future__ import annotations

import os
import sys
import uuid

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.normpath(os.path.join(_TEST_DIR, "..", "backend"))
os.chdir(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import delete, select  # noqa: E402

from app.database import SessionLocal, bind_tenant  # noqa: E402
from app.models import LlmCorrection, Rule, RuleSet, Tenant  # noqa: E402
from app.services import correction_ledger  # noqa: E402
from app.services.rule_import_service import import_rules_from_text  # noqa: E402

RULE_TEXT = "发票价税合计应等于报销单报销金额，金额不一致时视为不通过。"
CATEGORIES = ["齐套性", "基础判断", "信息准确性", "时间逻辑"]
# 人工修正要"合理才可信"：本租户把金额一致类规则归到哪个类别是一种口径选择，
# 从最可能的口径开始试（金额比对归"基础判断"是常见口径；归"齐套性"语义不通，
# 模型有权拒绝——那种情况不算回灌失败，测试也不该用它）。
CORRECTION_CANDIDATES = ["基础判断", "时间逻辑", "信息准确性", "齐套性"]


def _import_once(db, tenant_id, rule_set_id) -> str:
    """导入一次，返回新建规则的 check_category。"""
    result = import_rules_from_text(db, tenant_id, rule_set_id, RULE_TEXT)
    if result.get("errors"):
        raise RuntimeError(f"解析报错: {result['errors']}")
    rules = db.execute(
        select(Rule).where(Rule.rule_set_id == rule_set_id).order_by(Rule.rule_no.desc())
    ).scalars().all()
    if not rules:
        raise RuntimeError(f"没有解析出规则：{result}")
    return str(rules[0].check_category or "")


def _clear_rules(db, rule_set_id) -> None:
    db.execute(delete(Rule).where(Rule.rule_set_id == rule_set_id))
    db.commit()


def main() -> int:
    db = SessionLocal()
    tenant_id = db.execute(
        select(Tenant.id).where(Tenant.code == "default")
    ).scalar_one_or_none()
    if tenant_id is None:
        print("[FAIL] 找不到 default 租户（先跑一次后端初始化）")
        return 2
    bind_tenant(db, tenant_id)

    rule_set = RuleSet(
        tenant_id=tenant_id,
        name=f"闭环验证-{uuid.uuid4().hex[:6]}",
        description="rule_loop_live",
        is_default=False,
    )
    db.add(rule_set)
    db.commit()
    db.refresh(rule_set)

    try:
        print("① 第一次解析（账本为空）…")
        c_model = _import_once(db, tenant_id, rule_set.id)
        print(f"   模型给出 check_category = {c_model!r}")
        if c_model not in CATEGORIES:
            print(f"   [WARN] 模型给出的类别不在预设清单里，按原值继续：{c_model!r}")

        c_human = next(
            (c for c in CORRECTION_CANDIDATES if c != c_model), CORRECTION_CANDIDATES[0]
        )
        print(f"② 模拟人工修正：check_category {c_model!r} → {c_human!r}（写进修正账本）")
        correction_ledger.record_correction(
            db,
            tenant_id,
            scope="rule_edit",
            field="check_category",
            before=c_model,
            after=c_human,
            rule_set_id=rule_set.id,
            target_type="rule",
            context=RULE_TEXT,
            note="rule_loop_live 模拟人工修正",
        )
        db.commit()
        digest = correction_ledger.corrections_digest(db, tenant_id, rule_set.id)
        print("   回灌清单：" + digest.replace("\n", " | "))

        _clear_rules(db, rule_set.id)
        print("③ 第二次解析同一段文本（账本已注入解析提示词）…")
        c_after = _import_once(db, tenant_id, rule_set.id)
        print(f"   模型给出 check_category = {c_after!r}")

        if c_after == c_human:
            print("\n[PASS] 人工修正已生效：同一段规则文本，模型这次按人工结论解析。")
            return 0
        print(
            f"\n[FAIL] 回灌未生效：期望 {c_human!r}，实际 {c_after!r}"
            "（检查 correction_ledger 是否注入了解析提示词）"
        )
        return 1
    finally:
        db.execute(delete(LlmCorrection).where(LlmCorrection.rule_set_id == rule_set.id))
        db.execute(delete(Rule).where(Rule.rule_set_id == rule_set.id))
        rs = db.execute(select(RuleSet).where(RuleSet.id == rule_set.id)).scalars().first()
        if rs is not None:
            db.delete(rs)
        db.commit()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
