# DocGraph 前端镜像：node 构建静态产物 → nginx 托管，并把 /api 反代到 backend 服务。
FROM public.ecr.aws/docker/library/node:22-slim AS build

ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build

# 先装依赖（只要锁文件没变就命中缓存），再拷源码
COPY package.json package-lock.json ./
RUN npm config set registry "${NPM_REGISTRY}" \
 && npm ci --no-audit --no-fund

COPY . .
RUN npm run build

FROM public.ecr.aws/docker/library/nginx:1.27-alpine

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
