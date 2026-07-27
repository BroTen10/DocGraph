"""端到端验收脚本（零第三方依赖，纯标准库 urllib）。
用主样本 24HCSP012260253 跑完整链路，对照 PRD 9.1 AC1-AC12。
流程：创建规则集 -> 灌 seed_rules -> 上传样本 -> review/start(内部OCR+fallback旧逻辑) -> 轮询 -> 落盘。
"""
from __future__ import annotations
import json, os, sys, time, urllib.request, urllib.error

BASE = "http://localhost:8800"
SAMPLE_DIR = r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\20260710资料样本\合同号 24HCSP012260253"
OUT = r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\acceptance_output"
os.makedirs(OUT, exist_ok=True)
BOUNDARY = "----acceptboundaryxxxx"


def log(*a):
    print("[ACCEPT]", *a, flush=True)


def _post_json(url, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_multipart(url, files):
    """files: list of (field_name, filename, bytes, mime)."""
    parts = []
    for field, fn, data, mime in files:
        parts.append(b"--" + BOUNDARY.encode() + b"\r\n")
        parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (field, fn)).encode("utf-8"))
        parts.append(("Content-Type: %s\r\n\r\n" % mime).encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    body = b"".join(parts) + b"--" + BOUNDARY.encode() + b"--\r\n"
    req = urllib.request.Request(url, data=body,
                               headers={"Content-Type": "multipart/form-data; boundary=%s" % BOUNDARY},
                               method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


# 1. 创建规则集
def create_ruleset():
    rs = _post_json(f"{BASE}/api/rule-sets",
                     {"name": "验收-出口代理-24HCSP012260253", "description": "PRD 9.1 功能验收", "is_default": True})
    log("规则集:", rs["id"], rs["name"])
    return rs["id"]


# 2. 灌 seed_rules
def seed_rules(rule_set_id):
    sys.path.insert(0, r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\backend")
    from app.services.seed_rules import ALL_SEED_RULES
    ok = 0
    fails = []
    for rule in ALL_SEED_RULES:
        try:
            _post_json(f"{BASE}/api/rules?rule_set_id={rule_set_id}", rule)
            ok += 1
        except Exception as e:
            fails.append((rule["doc_type"], rule["check_category"], str(e)[:120]))
    log(f"规则灌入: {ok}/{len(ALL_SEED_RULES)}  失败 {len(fails)}")
    for f in fails:
        log("   !!", f)
    return ok


# 3. 上传样本
def upload(rule_set_id):
    files = []
    for fn in sorted(os.listdir(SAMPLE_DIR)):
        if fn.lower() == "thumbs.db":
            continue
        p = os.path.join(SAMPLE_DIR, fn)
        if not os.path.isfile(p):
            continue
        lower = fn.lower()
        mime = "application/pdf" if lower.endswith(".pdf") else ("image/jpeg" if lower.endswith((".jpg", ".jpeg")) else "image/png")
        with open(p, "rb") as fh:
            files.append(("files", fn, fh.read(), mime))
    log(f"准备上传 {len(files)} 个文件")
    data = _post_multipart(f"{BASE}/api/contracts/upload?rule_set_id={rule_set_id}", files)
    with open(os.path.join(OUT, "01_upload.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log("上传 contract_id:", data.get("contract_id"))
    return data["contract_id"]


# 4. 启动审查
def start_review(contract_id):
    t = _post_json(f"{BASE}/api/reviews/start", {"contract_id": str(contract_id), "snapshot_id": None})
    log("审查任务:", t["id"], "status=", t.get("status"))
    return t["id"]


# 5. 轮询
def poll(task_id, max_wait=900):
    url = f"{BASE}/api/reviews/{task_id}"
    start = time.time()
    last = None
    while time.time() - start < max_wait:
        t = _get_json(url)
        stage = t.get("stage")
        if stage != last:
            log(f"  进度 {t.get('progress')}% | {stage} | {t.get('status')}")
            last = stage
        if t.get("status") in ("completed", "failed"):
            return t
        time.sleep(5)
    raise TimeoutError("审查超时")


# 6. 取结果
def fetch_results(task_id):
    by_rule = _get_json(f"{BASE}/api/reviews/{task_id}/by-rule")
    by_doc = _get_json(f"{BASE}/api/reviews/{task_id}/by-doc")
    with open(os.path.join(OUT, "02_by_rule.json"), "w", encoding="utf-8") as f:
        json.dump(by_rule, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "03_by_doc.json"), "w", encoding="utf-8") as f:
        json.dump(by_doc, f, ensure_ascii=False, indent=2)
    return by_rule, by_doc


def main():
    rs_id = create_ruleset()
    seed_rules(rs_id)
    cid = upload(rs_id)
    task_id = start_review(cid)
    task = poll(task_id)
    log("最终:", task.get("status"), task.get("error"))
    if task.get("status") == "failed":
        return
    by_rule, by_doc = fetch_results(task_id)
    log("结果落盘 ->", OUT)
    log("by_rule 结果条数:", len(by_rule.get("results", [])), " by_doc 文档数:", len(by_doc.get("docs", [])))
    log("汇总:", by_rule.get("summary"))
    print("\n=== 文档分类概览（来自上传响应）===")
    for d in up.get("classified", []):
        print(f"  {str(d.get('file_name','?')):48s} | {str(d.get('doc_type','?')):14s} | required={d.get('is_required')}")


if __name__ == "__main__":
    main()
