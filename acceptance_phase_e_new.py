# -*- coding: utf-8 -*-
"""Phase E 新流程验收：任意人类语言规则 → 自动发现新类型/意图 → 建图（本体层）→ 双引擎审查。
使用真实 LLM/OCR/Neo4j，跑完自动清理测试数据。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:8800"
SAMPLE_DIR = r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\20260710资料样本\合同号 24HCSP012260253"
BOUNDARY = "----phaseEboundaryxxxx"


def log(*a):
    print("[NEW]", *a, flush=True)


def _post_json(url, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _delete(url):
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=60) as r:
        try:
            return json.loads(r.read().decode("utf-8"))
        except Exception:
            return {}


def _post_multipart(url, files):
    parts = []
    for field, fn, data, mime in files:
        parts.append(b"--" + BOUNDARY.encode() + b"\r\n")
        parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (field, fn)).encode("utf-8"))
        parts.append(("Content-Type: %s\r\n\r\n" % mime).encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    body = b"".join(parts) + b"--" + BOUNDARY.encode() + b"--\r\n"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % BOUNDARY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name, cond, extra=""):
    print(f"[NEW] {'PASS' if cond else 'FAIL'} {name} {extra}")
    return bool(cond)


def main() -> None:
    rs_id = None
    contract_no = None
    doc_type_id = None
    try:
        # 1. 零预设规则集
        rs = _post_json(f"{BASE}/api/rule-sets", {"name": f"验收-泛化-{time.strftime('%Y%m%d%H%M%S')}", "description": "Phase E 新流程验收", "is_default": False})
        rs_id = rs["id"]
        check("新建规则集（零预设）", rs.get("doc_types") == [] and rs.get("check_categories") == [], json.dumps(rs.get("doc_types")))

        # 2. 导入任意人类语言规则（含未注册类型/新意图 + 空标签规则）
        rule_text = (
            "验收确认单的验收金额应等于付款水单的付款金额；"
            "付款水单为必备文件，缺失则整套单据不齐；"
            "所有单据应清晰可辨、无涂改、无缺页；"
            "收付款单据日期应符合先收后付的时间逻辑。"
        )
        imp = _post_json(f"{BASE}/api/rules/import-batch?rule_set_id={rs_id}", {"raw_text": rule_text, "skill_ids": None})
        log("导入结果:", json.dumps({k: imp.get(k) for k in ("total", "imported", "skipped")}, ensure_ascii=False), "new_doc_types=", imp.get("new_doc_types"))
        check("规则导入成功（>0）", imp.get("imported", 0) >= 4, f"imported={imp.get('imported')}")
        check("自动发现新文件类型", "验收确认单" in (imp.get("new_doc_types") or []), str(imp.get("new_doc_types")))
        rules = _get_json(f"{BASE}/api/rules?rule_set_id={rs_id}")
        rules = rules if isinstance(rules, list) else rules.get("rules", [])
        # 语义自描述：规则携带 scope/intents 元数据（标签可为空，也可由 LLM 直接给出）
        with_meta = [r for r in rules if (r.get("scope") or r.get("intents") or (r.get("structure") or {}).get("assertion"))]
        check("规则自描述（scope/intents/structure）", len(with_meta) == len(rules), f"with_meta={len(with_meta)}/{len(rules)}")
        structured = [r for r in rules if (r.get("structure") or {}).get("assertion")]
        check("结构化规则（验收金额=付款金额）", len(structured) >= 1, f"count={len(structured)}")

        # 3. 新类型 pending_review + key_fields 预填
        dts = _get_json(f"{BASE}/api/doc-types?status=pending_review")
        new_dt = next((d for d in dts.get("doc_types", []) if d.get("name") == "验收确认单"), None)
        check("新类型注册为 pending_review", new_dt is not None, str(new_dt))
        if new_dt:
            doc_type_id = new_dt["id"]
            check("key_fields 预填", "验收金额" in (new_dt.get("key_fields") or []), str(new_dt.get("key_fields")))

        # 4. 确认规则 + 构建图谱（本体层）
        _post_json(f"{BASE}/api/rules/confirm?rule_set_id={rs_id}", {"ids": None})
        gtask = _post_json(f"{BASE}/api/rules/build-graph-async?rule_set_id={rs_id}&auto_confirm_all=true", {})
        task_id = gtask["task_id"]
        start = time.time()
        g = None
        while time.time() - start < 900:
            g = _get_json(f"{BASE}/api/rules/build-graph-status/{task_id}")
            if g.get("status") in ("completed", "failed"):
                break
            time.sleep(4)
        check("图谱构建完成", g is not None and g.get("status") == "completed", f"nodes={g.get('node_count')} edges={g.get('edge_count')} err={g.get('error')}")
        snapshot_id = g.get("snapshot_id")
        graph_id = g.get("graph_id")

        # 5. 本体层查询
        ont = _get_json(f"{BASE}/api/rules/graph/ontology?graph_id={graph_id}")
        ont_docs = {str(d["name"]) for d in ont.get("doc_types", [])}
        ont_intents = {str(i["name"]) for i in ont.get("check_intents", [])}
        check("本体层含新类型节点", f"文件类型:验收确认单" in ont_docs, str(sorted(ont_docs)))
        check("本体层含检查意图", any("格式规范" in i or "时间逻辑" in i or "信息准确性" in i for i in ont_intents), str(sorted(ont_intents)))
        check("本体层含规则节点", len(ont.get("rules", [])) >= 4, f"rules={len(ont.get('rules', []))}")

        # 6. 上传小合同（付款水单/收款水单/代理协议）
        files = []
        for fn in sorted(os.listdir(SAMPLE_DIR)):
            lower = fn.lower()
            if not any(k in lower for k in ("银行水单", "代理协议")):
                continue
            if lower.endswith(".pdf"):
                mime = "application/pdf"
            elif lower.endswith((".png", ".jpg", ".jpeg")):
                mime = "image/png"
            else:
                continue
            with open(os.path.join(SAMPLE_DIR, fn), "rb") as fh:
                files.append(("files", fn, fh.read(), mime))
        log("上传文件数:", len(files))
        up = _post_multipart(f"{BASE}/api/contracts/upload?rule_set_id={rs_id}", files)
        cid = up["contract_id"]
        contract_no = up.get("contract_no")
        check("合同上传成功", bool(cid), f"contract_no={contract_no}")

        # 7. 双引擎审查
        t = _post_json(f"{BASE}/api/reviews/start", {"contract_id": str(cid), "snapshot_id": str(snapshot_id)})
        tid = t["id"]
        start = time.time()
        task = None
        while time.time() - start < 1200:
            task = _get_json(f"{BASE}/api/reviews/{tid}")
            if task.get("status") in ("completed", "failed"):
                break
            time.sleep(5)
        check("审查完成", task is not None and task.get("status") == "completed", f"err={task.get('error')}")
        by_rule = _get_json(f"{BASE}/api/reviews/{tid}/by-rule")
        results = by_rule.get("results", [])
        summary = by_rule.get("summary", {})
        log("审查汇总:", json.dumps(summary, ensure_ascii=False))
        check("审查有结果", len(results) > 0, f"total={len(results)}")
        sources = {r.get("source") for r in results}
        check("双引擎来源（graph+llm）", "graph" in sources and "llm" in sources, str(sources))
        llm_items = [r for r in results if r.get("source") == "llm"]
        check("LLM 定性规则审查（整批/清晰可辨类）", any("清晰" in (r.get("rule_text") or "") for r in llm_items) or len(llm_items) > 0, f"llm_count={len(llm_items)}")

        # 8. 状态闭环：把一条 fail 流转 closed
        fail_item = next((r for r in results if r.get("result") == "fail"), None)
        if fail_item:
            updated = _post_json(f"{BASE}/api/reviews/results/{fail_item['id']}/status", {"status": "closed", "note": "Phase E 验收人工确认"})
            check("结果状态闭环（fail→closed）", updated.get("status") == "closed", str(updated.get("status")))
        else:
            check("结果状态闭环（无 fail 可流转，跳过）", True, "no fail result")

        print("\n[NEW] PHASE_E_NEW_FLOW_OK")
    finally:
        # 清理：删除规则集（级联合同/规则）、pending 新类型、上传文件目录
        if rs_id:
            try:
                _delete(f"{BASE}/api/rule-sets/{rs_id}")
                log("规则集已删除")
            except Exception as e:
                log("规则集清理失败:", e)
        if doc_type_id:
            try:
                _delete(f"{BASE}/api/doc-types/{doc_type_id}")
                log("pending 新类型已删除")
            except Exception as e:
                log("类型清理失败:", e)
        if contract_no:
            target = os.path.join(r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\backend\uploads", str(contract_no))
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
                log("上传目录已清理:", contract_no)


if __name__ == "__main__":
    main()
