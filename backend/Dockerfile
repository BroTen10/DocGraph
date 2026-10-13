# DocGraph 后端镜像（Linux）。数据库不在本镜像内，由 docker-compose 提供。
#
# 基础镜像统一走 public.ecr.aws（Docker Hub 在部分网络下不可达），
# apt / pip 源可用 --build-arg 覆盖，例如大陆服务器：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
FROM public.ecr.aws/docker/library/python:3.12-slim

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# libgl1 / libglib2.0-0 / libxcb1：cv2（rapidocr 扫描件 bbox）在 slim 镜像里缺少这些 .so，
# 不加会在 OCR 布局分析时报 ImportError: libxcb.so.1。
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    else \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 tzdata; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖再拷源码，改代码时可复用依赖层
COPY requirements.txt ./
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r requirements.txt

COPY . .

# UPLOAD_ROOT 由 compose 注入为 /data/uploads 并挂卷持久化
RUN mkdir -p /data/uploads

EXPOSE 8800

# 健康检查：容器内不装 curl，直接用 python 打自家的 /api/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/api/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800", "--proxy-headers"]
