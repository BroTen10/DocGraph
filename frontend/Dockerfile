# DocGraph 前端镜像：node 构建静态产物 → nginx 托管，并把 /api 反代到 backend 服务。
FROM public.ecr.aws/docker/library/node:22-slim AS build

ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build

# 先装依赖（只要锁文件没变就命中缓存），再拷源码
COPY package.json package-lock.json ./
RUN npm config set registry "${NPM_REGISTRY}" \
 && npm ci --no-audit --no-fund

COPY . .

# 前端构建期版本信息：与后端同一批 build args（deploy.sh 注入），
# vite.config.ts 读 process.env.DOCGRAPH_* 后注入 __BUILD_INFO__，
# 页脚据此显示"前端构建版本"，并与后端 /api/version 比对是否一致。
ARG DOCGRAPH_APP_VERSION=""
ARG DOCGRAPH_GIT_COMMIT=""
ARG DOCGRAPH_GIT_DESCRIBE=""
ARG DOCGRAPH_GIT_TAG=""
ARG DOCGRAPH_GIT_BRANCH=""
ARG DOCGRAPH_GIT_DIRTY=""
ARG DOCGRAPH_BUILD_TIME=""
ARG DOCGRAPH_BUILD_ENV="prod"

ENV DOCGRAPH_APP_VERSION=${DOCGRAPH_APP_VERSION} \
    DOCGRAPH_GIT_COMMIT=${DOCGRAPH_GIT_COMMIT} \
    DOCGRAPH_GIT_DESCRIBE=${DOCGRAPH_GIT_DESCRIBE} \
    DOCGRAPH_GIT_TAG=${DOCGRAPH_GIT_TAG} \
    DOCGRAPH_GIT_BRANCH=${DOCGRAPH_GIT_BRANCH} \
    DOCGRAPH_GIT_DIRTY=${DOCGRAPH_GIT_DIRTY} \
    DOCGRAPH_BUILD_TIME=${DOCGRAPH_BUILD_TIME} \
    DOCGRAPH_BUILD_ENV=${DOCGRAPH_BUILD_ENV}

RUN npm run build

FROM public.ecr.aws/docker/library/nginx:1.27-alpine

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80
