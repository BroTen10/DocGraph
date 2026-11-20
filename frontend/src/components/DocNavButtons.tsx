/**
 * 文档对照的「上一张 / 下一张」切换控件（抽屉顶栏右上角）。
 *
 * 只负责在调用方给定的清单内前后移动：清单由页面按业务边界给出
 * （某条判定涉及的原件 / 某次上传合同下的文件），
 * 组件自己不会去取清单之外的文档，清单少于 2 份或不含当前文档时不渲染。
 */

import { Button, Space, Tooltip, Typography } from 'antd'
import { LeftOutlined, RightOutlined } from '@ant-design/icons'

const { Text } = Typography

/** 对照视图里可切换浏览的文档 */
export interface CompareNavDoc {
  id: string
  file_name: string
}

export interface DocNavButtonsProps {
  /** 可切换的文档清单（含当前文档） */
  docs: CompareNavDoc[]
  /** 当前正在查看的文档 id */
  currentId?: string | null
  /** 切换回调：由页面负责加载目标文档 */
  onNavigate: (docId: string) => void
  /** 范围说明（tooltip 文案），如「本检查项涉及文件」 */
  scopeLabel?: string
}

export default function DocNavButtons({
  docs,
  currentId,
  onNavigate,
  scopeLabel,
}: DocNavButtonsProps) {
  const index = currentId ? docs.findIndex((item) => item.id === currentId) : -1
  if (index < 0 || docs.length < 2) return null

  const total = docs.length
  const prev = index > 0 ? docs[index - 1] : null
  const next = index < total - 1 ? docs[index + 1] : null

  return (
    <Space size={4} wrap={false}>
      <Tooltip title={prev ? `上一张：${prev.file_name}` : '已是第一张'}>
        <Button
          size="small"
          icon={<LeftOutlined />}
          disabled={!prev}
          aria-label="上一张文档"
          onClick={() => prev && onNavigate(prev.id)}
        >
          上一张
        </Button>
      </Tooltip>
      <Tooltip
        title={`${scopeLabel ? `${scopeLabel} · ` : ''}共 ${total} 份，当前第 ${index + 1} 份：${docs[index].file_name}`}
      >
        <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
          {index + 1}/{total}
        </Text>
      </Tooltip>
      <Tooltip title={next ? `下一张：${next.file_name}` : '已是最后一张'}>
        <Button
          size="small"
          disabled={!next}
          aria-label="下一张文档"
          onClick={() => next && onNavigate(next.id)}
        >
          下一张 <RightOutlined />
        </Button>
      </Tooltip>
    </Space>
  )
}
