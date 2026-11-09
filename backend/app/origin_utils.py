"""Origin 归一化：配置校验与写请求来源校验共用。"""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_origin(value: str | None) -> str | None:
    """把 ``scheme://host[:port]`` 归一化；非法来源返回 ``None``。

    浏览器发出的 Origin 会省略默认端口（http:80 / https:443）、主机名小写且
    不带路径。这里对两侧做同样处理，避免仅因写法差异把合法来源误判为非法。
    """
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        # 允许配置值带一个结尾斜杠，但不接受路径、查询串或片段。
        if (
            parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    if parsed.scheme == "http" and port == 80:
        port = None
    elif parsed.scheme == "https" and port == 443:
        port = None
    # IPv6 字面量在 URL 里必须带方括号
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}" + (f":{port}" if port else "")


def origin_of_url(url: str | None) -> str | None:
    """从完整 URL 中提取归一化来源（忽略路径、查询串和片段）。"""
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return normalize_origin(f"{parsed.scheme}://{parsed.netloc}")
