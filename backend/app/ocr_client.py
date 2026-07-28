"""OCR 客户端封装（阿里云百炼 - 通义千问 VL，OpenAI 兼容端点）。

一次调用完成：文字识别 + 印章检测 + 字段提取 + 语义理解。
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from pathlib import Path
from typing import Optional

from .llm_client import LLMClient, LLMError
from .config import settings

logger = logging.getLogger(__name__)


class OCRClient:
    """通义千问 VL OCR 客户端。复用 LLMClient 的 OpenAI 兼容调用能力。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._client = LLMClient(
            api_key=api_key or settings.ocr_api_key,
            base_url=base_url or settings.ocr_base_url,
            model=model or settings.ocr_model_name,
        )
        self.model = self._client.model

    def _encode_image(self, image_path: str) -> str:
        """读取图片为 base64 data URL。"""
        p = Path(image_path)
        suffix = p.suffix.lower().lstrip(".")
        mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
        mime = mime_map.get(suffix, "image/png")
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def recognize(
        self,
        image_path: str,
        doc_type_hint: Optional[str] = None,
        field_template: Optional[list[str]] = None,
    ) -> dict:
        """对单张图片执行 OCR + 印章检测 + 字段提取。

        Args:
            image_path: 图片文件路径
            doc_type_hint: 文件业务类型提示（如"出口报关单"），帮助模型聚焦字段
            field_template: 需提取的字段名列表（如 ["合同协议号","境内发货人",...]），为空时自动推断类型+自由提取

        Returns:
            {
                "text": "整页文本",
                "has_stamp": True/False/None,
                "fields": {"字段名": "值", ..., "__inferred_doc_type__": "推断类型"},
                "inferred_doc_type": "模型推测的文档类型" or "",
                "confidence": 0.0-1.0,
                "low_confidence_fields": ["字段名", ...],
                "success": True/False,
                "error": "失败原因（仅 success=False 时）"
            }
        """
        try:
            data_url = self._encode_image(image_path)
        except Exception as e:
            logger.error("读取图片失败 %s: %s", image_path, e)
            return {"success": False, "error": f"读取图片失败: {e}"}

        hint = f"该文件类型为【{doc_type_hint}】。" if doc_type_hint else ""

        if field_template:
            # 已知文档类型：按模板提取 + 额外推断类型作为辅助信息
            fields_hint = "请重点提取以下字段：" + "、".join(field_template) + "。"
            infer_hint = "额外从文档内容和布局推断当前文件的业务类型名称，填入 inferred_doc_type。"
        else:
            # 未知文档类型：自由推断类型 + 自由提取结构化信息
            fields_hint = ""
            infer_hint = (
                "该文件业务类型未知。请从文档内容和布局推断文件的业务类型名称"
                "（如'采购合同'、'装箱单'、'运单'等），填入 inferred_doc_type。"
                "同时提取文档中任何明显的结构化信息（表格头、键值对、表单字段等），填入 fields 对象。"
            )

        system_prompt = (
            "你是一个专业的贸易单证 OCR 助手。对图片执行：\n"
            "1. 识别全部可见文字；\n"
            "2. 判断图片中是否存在印章（红色圆形/椭圆形印章图形，has_stamp: true=有/false=无/null=无法判断）；\n"
            "3. 按要求提取结构化字段；\n"
            "4. 推断当前文档的业务类型名称，填入 inferred_doc_type；\n"
            "5. 给出整体置信度 confidence（0-1）和低置信度字段列表 low_confidence_fields。\n"
            "严格输出 JSON，schema: {\"text\":string,\"has_stamp\":bool|null,\"fields\":object,"
            "\"inferred_doc_type\":string|null,\"confidence\":number,\"low_confidence_fields\":[string]}"
        )
        user_prompt = f"{hint}{fields_hint}{infer_hint}请识别这张图片。"

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        try:
            result = self._client.chat_json(messages=messages, temperature=0.1, max_tokens=4096)
        except LLMError as e:
            logger.error("OCR 调用失败 %s: %s", image_path, e)
            return {"success": False, "error": f"OCR 调用失败: {e}"}
        except Exception as e:
            logger.error("OCR 未知错误 %s: %s", image_path, e)
            return {"success": False, "error": f"OCR 未知错误: {e}"}

        # 把 inferred_doc_type 注入到 fields 里，让下游无缝存储到 DB
        inferred = result.get("inferred_doc_type") or ""
        fields_dict = result.get("fields", {}) or {}
        if inferred:
            fields_dict["__inferred_doc_type__"] = inferred

        # 标准化输出
        return {
            "text": result.get("text", ""),
            "has_stamp": result.get("has_stamp"),
            "fields": fields_dict,
            "inferred_doc_type": inferred,
            "confidence": float(result.get("confidence", 0.0)),
            "low_confidence_fields": result.get("low_confidence_fields", []) or [],
            "success": True,
        }


_global_ocr: Optional[OCRClient] = None
_global_ocr_lock = threading.Lock()


def get_ocr_client() -> OCRClient:
    """全局单例 OCR 客户端。"""
    global _global_ocr
    if _global_ocr is not None:
        return _global_ocr
    with _global_ocr_lock:
        if _global_ocr is None:
            _global_ocr = OCRClient()
        return _global_ocr
