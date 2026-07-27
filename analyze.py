"""分析已落盘的验收结果，对照 PRD 9.1 AC1-AC12。不重跑系统，只读 acceptence_output。"""
import json
from collections import defaultdict

OUT = r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\acceptance_output"
br = json.load(open(f"{OUT}/02_by_rule.json", encoding="utf-8"))
bd = json.load(open(f"{OUT}/03_by_doc.json", encoding="utf-8"))

print("=== 汇总 ===")
print("by_rule summary:", br["summary"])
print("by_doc  summary:", bd["summary"])
print("by_doc docs 条数:", len(bd.get("docs", [])))
if bd.get("docs"):
    print("docs[0] 字段:", list(bd["docs"][0].keys()))

print("\n=== 按 check_category 统计 pass/fail/unverifiable ===")
cat = defaultdict(lambda: defaultdict(int))
for r in br["results"]:
    cat[r["check_category"]][r["result"]] += 1
for c, d in cat.items():
    print(f"  {c}: {dict(d)}")

print("\n=== FAIL / UNVERIFIABLE 完整详情 ===")
for r in br["results"]:
    if r["result"] in ("fail", "unverifiable"):
        print(f"\n[{r['result'].upper()}] {r['doc_type']} / {r['check_category']}")
        print("  doc :", r.get("doc_name"))
        print("  issue:", r.get("issue_desc"))
        det = r.get("detail")
        print("  detail:", json.dumps(det, ensure_ascii=False)[:600] if det else None)
        print("  sugg :", (r.get("suggestion") or "")[:300])

print("\n=== by_doc 文档 OCR/字段概览 ===")
for d in bd.get("docs", []):
    ocr = d.get("ocr_status") or d.get("ocrStatus")
    conf = d.get("ocr_confidence") or d.get("ocrConfidence")
    dt = d.get("doc_type") or d.get("docType")
    fn = d.get("file_name") or d.get("fileName") or d.get("doc_name")
    print(f"  {str(fn):46s} | {str(dt):12s} | ocr={ocr} conf={conf}")
