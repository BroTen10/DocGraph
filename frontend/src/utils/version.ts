/** 版本信息：前端构建期注入的自身信息 + 后端 /api/version，供页脚徽标与系统设置页共用。 */

import { useEffect, useState } from 'react'
import { versionApi } from '../api/client'
import type { BuildInfo } from '../types'

/** 前端自身构建信息（vite.config.ts 在构建期注入） */
export const FRONTEND_BUILD_INFO: BuildInfo = __BUILD_INFO__

/** 取不到任何版本信息时的兜底版本号（后端 app/version.py 的 FALLBACK_VERSION 同值） */
export const FALLBACK_VERSION = '0.0.0'

/** 构建环境的中文说明 */
export function envText(env: string | null | undefined): string {
  if (env === 'dev') return '本机开发'
  if (env === 'prod') return '正式环境'
  return '未知'
}

/** 未提交改动的中文说明（git 不可用时为"未知"，不假装干净） */
export function dirtyText(dirty: boolean | null | undefined): string {
  if (dirty === true) return '有（代码未提交）'
  if (dirty === false) return '无'
  return '未知'
}

/** 版本来源的中文说明（说明这份版本号是从哪儿读到的） */
export function sourceText(source: string | null | undefined): string {
  switch (source) {
    case 'build-args':
      return '构建注入（部署脚本）'
    case 'runtime-git':
      return '运行时读 git'
    case 'version-file':
      return 'VERSION 文件'
    default:
      return '未知'
  }
}

export interface VersionState {
  frontend: BuildInfo
  backend: BuildInfo | null
  /** 后端 /api/version 取不到 */
  backendFailed: boolean
  /** 后端仍在读取中 */
  loading: boolean
  /**
   * 前端资源与后端接口不是同一次构建：
   * 版本号不同，或两侧都取到提交号时提交号不同。信息缺失时不算不一致（不误报）。
   */
  mismatch: boolean
  /** 两侧都没有可用版本信息（例如未经 deploy.sh 构建的镜像） */
  unknown: boolean
  /** 有版本号但至少一侧拿不到提交号：只能比版本号 */
  partial: boolean
}

/** 拉取后端版本并与前端自身构建信息比对（组件挂载时拉一次） */
export function useVersionInfo(): VersionState {
  const [backend, setBackend] = useState<BuildInfo | null>(null)
  const [backendFailed, setBackendFailed] = useState(false)

  useEffect(() => {
    let alive = true
    versionApi
      .get()
      .then((info) => {
        if (alive) setBackend(info)
      })
      .catch(() => {
        if (alive) setBackendFailed(true)
      })
    return () => {
      alive = false
    }
  }, [])

  const frontend = FRONTEND_BUILD_INFO
  const backendKnown = !!backend && backend.version !== FALLBACK_VERSION
  const frontendKnown = frontend.version !== FALLBACK_VERSION
  // 主判据是版本号：构建期注入 / VERSION 文件两侧都拿得到，不依赖 git
  const versionDiff = backendKnown && frontendKnown && backend.version !== frontend.version
  // 次判据是提交号：两侧都拿得到时才用（本机 git 不可用时不做误判）
  const commitDiff = !!backend?.commit && !!frontend.commit && backend.commit !== frontend.commit
  const mismatch = versionDiff || commitDiff

  return {
    frontend,
    backend,
    backendFailed,
    loading: !backend && !backendFailed,
    mismatch,
    unknown: !frontendKnown && !backendKnown,
    partial: !mismatch && !(frontend.commit && backend?.commit),
  }
}
