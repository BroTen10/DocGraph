"""OCR 模型冒烟测试：用真实样本校验当前 OCR 配置（阿里云百炼 qwen3.7-plus）可正常调用。

用法（仓库根目录执行）：
    backend\\.venv\\Scripts\\python.exe tests\\test_ocr_model.py

退出码：0 = 全部通过，1 = 失败。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # Settings 的 env_file=".env" 按相对路径加载 backend/.env

from app.config import settings  # noqa: E402
from app.ocr_client import get_ocr_client  # noqa: E402

SAMPLE_DIR = ROOT / "20260710资料样本" / "24HCSP000890404--出口"
SAMPLES = [
    ("24HCSP000890404收汇截图.jpg", "收汇截图"),
    ("24HCSP000890404付款水单.png", "付款水单"),
]


def main() -> int:
    print(f"[OCR] base_url = {settings.ocr_base_url}")
    print(f"[OCR] model    = {settings.ocr_model_name}")
    print(f"[OCR] api_key  = {'已配置' if settings.ocr_api_key else '未配置'}")

    if settings.ocr_model_name != "qwen3.7-plus":
        print(f"[FAIL] 当前 OCR 模型不是 qwen3.7-plus: {settings.ocr_model_name}")
        return 1
    if not settings.ocr_api_key:
        print("[FAIL] OCR API key 未配置")
        return 1

    client = get_ocr_client()
    for name, doc_type in SAMPLES:
        img = SAMPLE_DIR / name
        if not img.exists():
            print(f"[SKIP] 样本不存在: {img}")
            continue
        print(f"\n[OCR] 测试样本: {name}（类型提示: {doc_type}）")
        r = client.recognize(str(img), doc_type_hint=doc_type)
        print(f"  success = {r['success']}")
        if not r["success"]:
            print(f"  error   = {r['error']}")
            return 1
        text = (r.get("text") or "").strip()
        fields = r.get("fields") or {}
        print(f"  text_len = {len(text)}")
        print(f"  has_stamp = {r.get('has_stamp')}  confidence = {r.get('confidence')}")
        print(f"  inferred_doc_type = {r.get('inferred_doc_type')!r}")
        print(f"  fields = {fields}")
        if len(text) < 10:
            print("  [FAIL] 识别文本过短，视为识别失败")
            return 1

    print("\n[PASS] 全部 OCR 样本识别成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
