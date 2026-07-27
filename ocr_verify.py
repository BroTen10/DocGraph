"""独立 OCR 链路验证：上传样本 -> 触发合同级批量 OCR -> 轮询进度 -> 确认文档状态。

纯标准库 urllib，零依赖。验证后端独立 OCR 接口（非 review/start 内部 OCR）是否真能跑通。
"""
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "http://localhost:8800/api"
SAMPLE_DIR = r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\20260710资料样本\合同号 24HCSP012260253"

BOUNDARY = "----ocrverifyboundary7a3f"


def _mp(files):
    body = b""
    for f in files:
        name = os.path.basename(f)
        body += ("--%s\r\n" % BOUNDARY).encode("utf-8")
        body += ('Content-Disposition: form-data; name="files"; filename="%s"\r\n'
                 % name).encode("utf-8")
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        with open(f, "rb") as fh:
            body += fh.read()
        body += b"\r\n"
    body += ("--%s--\r\n" % BOUNDARY).encode("utf-8")
    return body


def req(method, path, params=None, data=None, headers=None, raw=False):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    r = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def upload(rule_set_id, files):
    body = _mp(files)
    _, d = req("POST", "/contracts/upload", params={"rule_set_id": rule_set_id},
               data=body, headers={"Content-Type": "multipart/form-data; boundary=%s" % BOUNDARY})
    return d


def main():
    # 1. 取一个规则集
    _, rs = req("GET", "/rule-sets")
    if not rs:
        print("无规则集，先退出"); return
    rs_id = rs[0]["id"]
    print("使用规则集:", rs[0]["name"], rs_id)

    # 2. 上传样本（全部 pending）
    files = [os.path.join(SAMPLE_DIR, f) for f in os.listdir(SAMPLE_DIR)
             if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".docx"))]
    print("上传文件数:", len(files))
    up = upload(rs_id, files)
    cid = up.get("contract_id")
    print("上传完成 contract_id:", cid, "| 文件数:", len(up.get("classified", [])))

    # 3. 触发合同级批量 OCR
    st, task = req("POST", "/ocr/contracts/%s" % cid, params={"rule_set_id": rs_id})
    print("触发响应:", st, "task_id:", task.get("id"), "total:", task.get("total_count"))
    tid = task["id"]

    # 4. 轮询进度
    print("\n--- 轮询 OCR 进度 ---")
    last = None
    for i in range(120):
        _, t = req("GET", "/ocr/tasks/%s" % tid)
        line = "status=%s progress=%d%% done=%d/%d success=%d failed=%d stage=%s" % (
            t.get("status"), t.get("progress", 0), t.get("done_count", 0),
            t.get("total_count", 0), t.get("success_count", 0),
            t.get("failed_count", 0), t.get("stage"))
        if line != last:
            print("[%3ds] %s" % (i * 3, line))
            last = line
        if t.get("status") in ("completed", "failed"):
            break
        time.sleep(3)

    # 5. 查文档最终状态
    _, det = req("GET", "/contracts/%s" % cid)
    docs = det.get("documents", [])
    from collections import Counter
    c = Counter(d.get("ocr_status") for d in docs)
    print("\n--- 文档 OCR 状态分布 ---", dict(c))
    fails = [d["file_name"] for d in docs if d.get("ocr_status") == "failed"]
    if fails:
        print("失败文件:", fails)
    print("\nVERIFY_DONE")


if __name__ == "__main__":
    main()
