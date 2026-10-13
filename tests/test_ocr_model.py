"""OCR 模型冒烟测试：用真实样本校验当前多模态 OCR 配置可正常调用。

用法（仓库根目录执行）：
    backend\\.venv\\Scripts\\python.exe tests\\test_ocr_model.py

可选：用 ``OCR_MODEL_ALLOWED``（逗号分隔）声明允许的模型清单，不在清单内直接判失败；
不设置则只校验模型名非空 + 真实调用成功，避免每次换模型都要改测试代码。

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

SAMPLE_DIR = ROOT / "20260710资料样本" / "24HCSP000000456--出口"
SAMPLES = [
    ("24HCSP000000456收汇截图.jpg", "收汇截图"),
    ("24HCSP000000456付款水单.png", "付款水单"),
]
UPLOAD_FALLBACK_DIR = BACKEND / "uploads"


def _resolve_samples() -> list[tuple[Path, str]]:
    """优先用固定样本；本机没有该样本目录时退回 uploads 里的第一张图。

    固定样本随业务资料入 gitignore，不一定每台机器都有；此时若直接跳过，
    会出现「零样本也算通过」的假绿，因此这里保证总能取到至少一张真实单据图。
    """
    found = [(SAMPLE_DIR / name, doc_type) for name, doc_type in SAMPLES if (SAMPLE_DIR / name).exists()]
    if found:
        return found
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        for path in sorted(UPLOAD_FALLBACK_DIR.rglob(pattern)):
            return [(path, "")]
    return []


def main() -> int:
    print(f"[OCR] base_url = {settings.ocr_base_url}")
    print(f"[OCR] model    = {settings.ocr_model_name}")
    print(f"[OCR] api_key  = {'已配置' if settings.effective_ocr_api_key else '未配置'}")

    if not settings.ocr_model_name:
        print("[FAIL] OCR 模型名为空（检查 OCR_MODEL_NAME / LLM_MODEL_NAME）")
        return 1
    allowed = {m.strip() for m in os.getenv("OCR_MODEL_ALLOWED", "").split(",") if m.strip()}
    if allowed and settings.ocr_model_name not in allowed:
        print(f"[FAIL] 当前 OCR 模型 {settings.ocr_model_name} 不在允许清单 {sorted(allowed)} 内")
        return 1
    if not settings.effective_ocr_api_key:
        print("[FAIL] OCR API key 未配置（OCR_API_KEY 与 LLM_API_KEY 均为空）")
        return 1

    samples = _resolve_samples()
    if not samples:
        print("[FAIL] 没有可用样本（固定样本目录与 backend/uploads 均为空），未真正验证 OCR")
        return 1

    client = get_ocr_client()
    for img, doc_type in samples:
        print(f"\n[OCR] 测试样本: {img}")
        if doc_type:
            print(f"  类型提示: {doc_type}")
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
