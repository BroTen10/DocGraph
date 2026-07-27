import json, urllib.request, urllib.error
from collections import defaultdict, Counter

BASE = "http://localhost:8800"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)

# 1) rule sets
sets = get("/api/rule-sets")
print(f"规则集数量: {len(sets)}\n")

def norm(s):
    return "".join(ch for ch in (s or "") if ch.isalnum()).lower()

for rs in sets:
    rsid = rs["id"]
    name = rs["name"]
    try:
        rules = get(f"/api/rules?rule_set_id={rsid}")
    except urllib.error.HTTPError as e:
        print(f"[{name}] 拉取失败: {e}")
        continue
    n = len(rules)
    print("=" * 70)
    print(f"规则集: {name}  (id={rsid[:8]}...)  规则数={n}")
    if n == 0:
        print("  (空)")
        continue

    # exact duplicate rule_text
    text_counter = Counter(norm(r.get("rule_text", "")) for r in rules)
    exact_dupes = {t: c for t, c in text_counter.items() if c > 1 and t}

    # same (doc_type, check_category, rule_text)
    triple_counter = Counter(
        (r.get("doc_type", ""), r.get("check_category", ""), norm(r.get("rule_text", "")))
        for r in rules
    )
    triple_dupes = {k: c for k, c in triple_counter.items() if c > 1}

    print(f"\n  [A] 完全相同 rule_text 的组数: {len(exact_dupes)}")
    for t, c in sorted(exact_dupes.items(), key=lambda x: -x[1]):
        print(f"      x{c}: {t[:60]}")

    print(f"\n  [B] 同(文件类型,检查项,文本)三元组重复组数: {len(triple_dupes)}")
    dup_rule_count = sum(c - 1 for c in triple_dupes.values())
    print(f"      含重复条目总数(超额): {dup_rule_count}")
    for (dt, cc, t), c in sorted(triple_dupes.items(), key=lambda x: -x[1]):
        print(f"      x{c}: [{dt}/{cc}] {t[:50]}")

    # near-duplicate detection: group by doc_type+check_category, compare normalized texts by token overlap
    print(f"\n  [C] 近义/高度相似(同文件类型+检查项, 文本词重叠>=0.8):")
    by_group = defaultdict(list)
    for r in rules:
        by_group[(r.get("doc_type", ""), r.get("check_category", ""))].append(r)
    near = []
    for (dt, cc), grp in by_group.items():
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                ti = set(norm(grp[i].get("rule_text", "")))
                tj = set(norm(grp[j].get("rule_text", "")))
                if not ti or not tj:
                    continue
                overlap = len(ti & tj) / max(len(ti), len(tj))
                if overlap >= 0.8 and ti != tj:
                    near.append((overlap, dt, cc, grp[i].get("rule_text", ""), grp[j].get("rule_text", "")))
    near.sort(reverse=True)
    if near:
        for ov, dt, cc, a, b in near:
            print(f"      重叠{ov:.2f} [{dt}/{cc}]")
            print(f"         · {a[:70]}")
            print(f"         · {b[:70]}")
    else:
        print("      无")

    print()
