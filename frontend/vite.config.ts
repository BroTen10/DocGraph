import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 前端构建期版本信息（页脚徽标与"前后端版本一致性"提示使用）。
 *
 * 取值优先级：部署脚本注入的环境变量（Docker 构建，见 frontend/Dockerfile）
 * > 仓库根 VERSION 文件 + 本机 git（本地 npm run build / vite dev）。
 * 两者都取不到时如实返回 unknown，不伪造版本号。
 */
function collectBuildInfo() {
  const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const env = process.env

  const fromGit = (args: string[]): string => {
    try {
      return execFileSync('git', ['-C', repoRoot, ...args], { encoding: 'utf8' }).trim()
    } catch {
      return ''
    }
  }

  let version = (env.DOCGRAPH_APP_VERSION || '').trim()
  if (!version) {
    const versionFile = resolve(repoRoot, 'VERSION')
    if (existsSync(versionFile)) version = readFileSync(versionFile, 'utf8').trim()
  }

  const commit = (env.DOCGRAPH_GIT_COMMIT || '').trim() || fromGit(['rev-parse', '--short', 'HEAD'])
  const dirtyEnv = (env.DOCGRAPH_GIT_DIRTY || '').trim()
  const dirty = dirtyEnv
    ? dirtyEnv.toLowerCase() === 'true'
    : commit !== '' && fromGit(['status', '--porcelain']) !== ''

  return {
    kind: 'frontend',
    name: 'DocGraph',
    version: version || '0.0.0',
    label: commit ? `${version || '0.0.0'}+${commit}${dirty ? '.dirty' : ''}` : version || '0.0.0',
    describe:
      (env.DOCGRAPH_GIT_DESCRIBE || '').trim() ||
      fromGit(['describe', '--tags', '--dirty', '--always']) ||
      null,
    tag: (env.DOCGRAPH_GIT_TAG || '').trim() || null,
    commit: commit || null,
    branch:
      (env.DOCGRAPH_GIT_BRANCH || '').trim() ||
      fromGit(['rev-parse', '--abbrev-ref', 'HEAD']) ||
      null,
    dirty: commit ? dirty : null,
    build_env: (env.DOCGRAPH_BUILD_ENV || '').trim() || (commit ? 'dev' : 'unknown'),
    built_at: (env.DOCGRAPH_BUILD_TIME || '').trim() || new Date().toISOString(),
    source: env.DOCGRAPH_APP_VERSION
      ? 'build-args'
      : commit
        ? 'runtime-git'
        : version
          ? 'version-file'
          : 'unknown',
  }
}

const buildInfo = collectBuildInfo()

// 后端默认在 http://localhost:8800
export default defineConfig({
  plugins: [react()],
  define: {
    // 前端构建信息；类型见 src/types.ts 的 BuildInfo（declare 在 src/vite-env.d.ts）
    __BUILD_INFO__: JSON.stringify(buildInfo),
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        // 优先用 start.bat 注入的实际后端地址（VITE_API_TARGET），
        // 端口冲突自动避让后前端能自动跟随，无需手改本文件。
        target: process.env.VITE_API_TARGET || 'http://localhost:8800',
        changeOrigin: true,
      },
    },
  },
})
