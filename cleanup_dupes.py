"""清理规则集内的三元组重复：保留每组第 1 条，删除其余。
用法: backend/.venv/Scripts/python.exe cleanup_dupes.py <rule_set_id>

API BASE: http://localhost:8800
"""

import json, sys, uuid
from collections import defaultdict
from urllib.request import Request, urlopen

BASE = "http://localhost:8800"

def api(method, path, data=None):
    url = BASE + path
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=30) as r:
        return json.load(r)

def norm(s):
    return "".join(ch for ch in (s or "") if ch.isalnum()).lower()

rsid = sys.argv[1] if len(sys.argv) > 1 else "be2467a2-807f-4fde-a7b4-723c9f6192dd"
print(f"清理规则集: {rsid}")

# 1) 拉取所有规则
rules = api("GET", f"/api/rules?rule_set_id={rsid}")
print(f"  当前规则数: {len(rules)}")

# 2) 按三元组分桶
groups = defaultdict(list)
for r in rules:
    key = (r["doc_type"], r["check_category"], norm(r["rule_text"]))
    groups[key].append(r["id"])

dups = {k: v for k, v in groups.items() if len(v) > 1}
print(f"  重复组数: {len(dups)}")

to_delete = []
for key, ids in dups.items():
    to_delete.extend(ids[1:])  # 保留第 1 条

print(f"  待删除 ID 数: {len(to_delete)}")

if not to_delete:
    print("  无重复，无需清理")
    sys.exit(0)

# 3) 分批删除（后端 DELETE /api/rules?ids= 接受逗号分隔，但 ID 太多可能 URL 超长）
batch_size = 100
total_deleted = 0
for i in range(0, len(to_delete), batch_size):
    batch = to_delete[i:i+batch_size]
    ids_str = ",".join(batch)
    result = api("DELETE", f"/api/rules?rule_set_id={rsid}&ids={ids_str}")
    total_deleted += result["deleted"]
    print(f"  已删除 {total_deleted}/{len(to_delete)}", end="\r")

print(f"\n  清理完成，共删除 {total_deleted} 条重复规则")

# 4) 最终计数
final = api("GET", f"/api/rules?rule_set_id={rsid}")
print(f"  去重后规则数: {len(final)}")
