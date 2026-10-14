import { Alert, Card, Descriptions, Tag } from 'antd'
import { dirtyText, envText, sourceText, useVersionInfo } from '../utils/version'

/**
 * 版本信息卡片：把"这套部署"的前后端版本摊开，方便与本机开发环境逐项对比。
 * 前后端提交号不一致时给出明确提示，避免把缓存问题误判成"服务器功能不对"。
 */
export default function VersionCard() {
  const { frontend, backend, backendFailed, loading, mismatch, unknown, partial } =
    useVersionInfo()

  return (
    <Card
      size="small"
      title="版本信息"
      style={{ marginBottom: 16 }}
      extra={
        mismatch ? (
          <Tag color="orange">前后端版本不一致</Tag>
        ) : unknown ? (
          <Tag>版本未知</Tag>
        ) : partial ? (
          <Tag>只能比对版本号</Tag>
        ) : (
          <Tag color="green">版本一致</Tag>
        )
      }
    >
      {mismatch && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="前端资源与后端接口不是同一次构建"
          description="常见原因是浏览器缓存着旧前端，或前端/后端镜像只重建了一个。请先强刷页面（Ctrl+F5）；若仍不一致，用 bash deploy.sh 重新构建两个镜像。"
        />
      )}
      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="后端版本">
          {backend?.label ?? (loading ? '读取中…' : '接口不可用')}
        </Descriptions.Item>
        <Descriptions.Item label="前端版本">{frontend.label}</Descriptions.Item>
        <Descriptions.Item label="后端提交">
          {backend?.describe || backend?.commit || '—'}
        </Descriptions.Item>
        <Descriptions.Item label="前端提交">
          {frontend.describe || frontend.commit || '—'}
        </Descriptions.Item>
        <Descriptions.Item label="后端环境">
          {backend ? envText(backend.build_env) : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="前端环境">{envText(frontend.build_env)}</Descriptions.Item>
        <Descriptions.Item label="后端构建时间">
          {backend?.built_at || backend?.commit_date || '—'}
        </Descriptions.Item>
        <Descriptions.Item label="前端构建时间">{frontend.built_at || '—'}</Descriptions.Item>
        <Descriptions.Item label="后端未提交改动">
          {backend ? dirtyText(backend.dirty) : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="前端未提交改动">{dirtyText(frontend.dirty)}</Descriptions.Item>
        <Descriptions.Item label="版本来源">
          {`后端 ${
            backend ? sourceText(backend.source) : backendFailed ? '接口不可用' : '读取中…'
          } / 前端 ${sourceText(frontend.source)}`}
        </Descriptions.Item>
        <Descriptions.Item label="最近发版 tag">
          {backend?.tag || frontend.tag || '—'}
        </Descriptions.Item>
      </Descriptions>
    </Card>
  )
}
