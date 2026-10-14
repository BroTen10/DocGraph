import { Tag, Tooltip } from 'antd'
import { dirtyText, envText, sourceText, useVersionInfo } from '../utils/version'

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div style={{ display: 'flex', gap: 8, lineHeight: '18px' }}>
      <span style={{ width: 56, color: '#94a3b8', flexShrink: 0 }}>{label}</span>
      <span style={{ wordBreak: 'break-all' }}>{value || '—'}</span>
    </div>
  )
}

/**
 * 版本徽标：显示前端自身构建版本，并拉取后端 /api/version 对比。
 *
 * 前后端提交号不一致时标橙——这是"浏览器缓存着旧前端"的典型症状，
 * 与服务器版本对不上时先看这里。
 */
export default function VersionBadge() {
  const { frontend, backend, backendFailed, loading, mismatch, unknown, partial } =
    useVersionInfo()

  const color = mismatch ? 'orange' : unknown || partial ? 'default' : 'green'
  const text = mismatch ? '版本不一致' : unknown ? '版本未知' : `v${frontend.version}`

  const tip = (
    <div style={{ fontSize: 12, minWidth: 250, maxWidth: 380 }}>
      {mismatch && (
        <div style={{ marginBottom: 6, color: '#faad14' }}>
          前端资源与后端接口不是同一次构建，请强刷页面（Ctrl+F5）或重新部署后再确认。
        </div>
      )}
      {partial && (
        <div style={{ marginBottom: 6, color: '#94a3b8' }}>
          至少一侧没有提交定位（本机 git 不可用，或镜像没经 deploy.sh 构建），只能比对版本号。
        </div>
      )}
      <Row label="前端" value={`${frontend.label} · ${envText(frontend.build_env)}`} />
      <Row
        label="前端提交"
        value={frontend.describe || frontend.commit || '未取到（本机 git 不可用）'}
      />
      <Row
        label="后端"
        value={
          backend
            ? `${backend.label} · ${envText(backend.build_env)}`
            : backendFailed
              ? '接口不可用'
              : loading
                ? '读取中…'
                : '—'
        }
      />
      <Row label="后端提交" value={backend?.describe || backend?.commit || '—'} />
      <Row label="后端构建" value={backend?.built_at || backend?.commit_date} />
      <Row label="前端构建" value={frontend.built_at} />
      <Row label="未提交改动" value={dirtyText(backend?.dirty ?? frontend.dirty)} />
      <Row
        label="来源"
        value={`前端 ${sourceText(frontend.source)} / 后端 ${
          backend ? sourceText(backend.source) : backendFailed ? '接口不可用' : '读取中…'
        }`}
      />
    </div>
  )

  return (
    <Tooltip title={tip} placement="bottomRight">
      <Tag color={color} style={{ cursor: 'help', marginInlineEnd: 0, fontSize: 11 }}>
        {text}
      </Tag>
    </Tooltip>
  )
}
