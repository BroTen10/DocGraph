"""使用 RapidOCR 提取文字行坐标。

多模态模型适合识别语义和字段，但其返回的 bbox 是估计值，页面越往下偏差越明显。
本模块用本地 OCR 引擎返回原生文本框，仅负责坐标定位，字段和语义仍由多模态模型完成。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_engine: Any = None
_engine_lock = threading.Lock()


def _get_engine():
    """延迟加载 RapidOCR 单例，避免应用启动时占用额外内存。"""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
            logger.info("RapidOCR 已加载，用于 OCR 坐标定位")
        return _engine


def extract_layout_lines(image_path: str, min_confidence: float = 0.35) -> list[dict]:
    """从图片提取文字行及归一化坐标。

    Returns:
        [{"text": str, "bbox": [left, top, right, bottom]}]，坐标均为 0-1 比例。
    """
    try:
        engine = _get_engine()
        result, _elapse = engine(image_path)
    except Exception:
        logger.exception("RapidOCR 坐标提取失败: %s", image_path)
        return []

    if not result:
        return []

    with Image.open(image_path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        return []

    lines: list[dict] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        points, text, score = item[0], item[1], item[2]
        text = str(text or "").strip()
        try:
            confidence = float(score)
        except (TypeError, ValueError):
            confidence = 0.0
        if not text or confidence < min_confidence or not points:
            continue

        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            continue

        x1 = max(0.0, min(1.0, min(xs) / width))
        y1 = max(0.0, min(1.0, min(ys) / height))
        x2 = max(0.0, min(1.0, max(xs) / width))
        y2 = max(0.0, min(1.0, max(ys) / height))
        if x2 - x1 < 0.002 or y2 - y1 < 0.002:
            continue

        lines.append(
            {
                "text": text,
                "bbox": [
                    round(x1, 6),
                    round(y1, 6),
                    round(x2, 6),
                    round(y2, 6),
                ],
            }
        )

    # 按阅读顺序排序，便于调试和匹配。
    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    return lines
