# -*- coding: utf-8 -*-
"""实测 rulesApi.importDocument 耗时与返回，以判断前端'点了没反馈'的真实原因。"""
import os, sys, json, time, urllib.request, urllib.error
from email.generator import BytesGenerator
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from io import BytesIO

BASE = "http://localhost:8801"
RULE_SET_ID = "45c768e6-870a-47f4-9615-bf9c66be4480"  # 出口代理-验证
PDF_PATH = r"D:\ProgramData\Trae\基于知识图谱的自动文档审查智能体\贸易合同过程文件校验说明 -简化.pdf"

def http_post_multipart(url, file_path, field_name="file"):
    # 构造 multipart/form-data
    boundary = "----WB" + str(int(time.time()))
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    body = BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="{field_name}"; filename="{os.path.basename(file_path)}"\r\n'.encode())
    body.write(b"Content-Type: application/octet-stream\r\n\r\n")
    body.write(file_bytes)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    data = body.getvalue()

    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body_bytes = r.read()
            dt = time.time() - t0
            return r.status, body_bytes, dt
    except urllib.error.HTTPError as e:
        dt = time.time() - t0
        return e.code, e.read(), dt
    except Exception as e:
        dt = time.time() - t0
        return -1, str(e).encode(), dt

if not os.path.exists(PDF_PATH):
    print("PDF not found:", PDF_PATH); sys.exit(1)

url = f"{BASE}/api/rules/import-document?rule_set_id={RULE_SET_ID}"
print(f"POST {url}")
print(f"file={os.path.basename(PDF_PATH)} size={os.path.getsize(PDF_PATH)} bytes")
print("calling ...")

# 后端路径前缀需要再确认一次
import urllib.request as _ur
print("\n--- 探一下接口路径 ---")
for prefix in ["/api/rules/rule-sets", "/api/rule-sets"]:
    u = f"{BASE}{prefix}"
    print(prefix, "->", end=" ")
    try:
        with _ur.urlopen(u + "/" + RULE_SET_ID, timeout=3) as r:
            print("ok", r.status)
    except Exception as e:
        print("err", str(e)[:80])

print("\n--- 实际打 import-document ---")
# 看后端 router 实际路径
status, body, dt = http_post_multipart(url, PDF_PATH)
print(f"status={status}  elapsed={dt:.2f}s  body_size={len(body)} bytes")
try:
    j = json.loads(body)
    print(json.dumps(j, ensure_ascii=False, indent=2)[:2000])
except Exception:
    print("raw:", body[:500])