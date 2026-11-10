import { Tag, Tooltip } from 'antd'
import { useVersionInfo } from '../utils/version'

/**
 * 版本徽标：只显示版本号与一致性状态色。
 *
 * 悬停不再展开提交号 / 构建时间 / 版本来源 / 未提交改动等构建细节 —— 那些对
 * 普通用户没有价值，只会造成困惑。完整信息统一在「系统设置 → 版本信息」
 * （VersionCard）里由运维按需查看。
 *
 * 唯一保留的悬停提示：前后端不是同一次构建时，告诉用户强刷页面。
 */
export default function VersionBadge() {
  const { frontend, mismatch, unknown, partial } = useVersionInfo()

  const color = mismatch ? 'orange' : unknown || partial ? 'default' : 'green'
  const text = mismatch ? '版本不一致' : unknown ? '版本未知' : `v${frontend.version}`

  if (!mismatch) {
    return (
      <Tag color={color} style={{ marginInlineEnd: 0, fontSize: 11 }}>
        {text}
      </Tag>
    )
  }

  return (
    <Tooltip
      title="前端资源与后端接口不是同一次构建，请强刷页面（Ctrl+F5）"
      placement="bottomRight"
    >
      <Tag color={color} style={{ cursor: 'help', marginInlineEnd: 0, fontSize: 11 }}>
        {text}
      </Tag>
    </Tooltip>
  )
}
