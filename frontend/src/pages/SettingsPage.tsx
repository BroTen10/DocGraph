import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import { settingsApi, getErrorMessage } from '../api/client'
import type { SettingsItem, PromptOptimizeResult } from '../types'
import PageHeader from '../components/PageHeader'

const { Text, Paragraph } = Typography
const { TextArea } = Input

const GROUP_ORDER = ['OCR 识别', '规则解析', '审查与建议', '文档类型', '图谱', '运行参数']
const ALL_GROUP = '全部'

type StatusFilter = 'all' | 'custom' | 'prompt' | 'param'

const STATUS_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '已自定义', value: 'custom' },
  { label: '提示词', value: 'prompt' },
  { label: '运行参数', value: 'param' },
]

const kindLabel = (item: SettingsItem) => (item.kind === 'text' ? '提示词' : '参数')

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsItem[]>([])
  const [loading, setLoading] = useState(false)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [optimizing, setOptimizing] = useState(false)
  const [optimizeTarget, setOptimizeTarget] = useState<SettingsItem | null>(null)
  const [optimizeResult, setOptimizeResult] = useState<PromptOptimizeResult | null>(null)
  const [optimizeDraft, setOptimizeDraft] = useState('')
  const [optimizeInstruction, setOptimizeInstruction] = useState('')
  const [activeGroup, setActiveGroup] = useState(ALL_GROUP)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [searchText, setSearchText] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])

  const syncSettings = (next: SettingsItem[]) => {
    setSettings(next)
    setValues(Object.fromEntries(next.map((s) => [s.key, s.value])))
  }

  const load = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.list()
      syncSettings(res.settings)
    } catch (e) {
      message.error('加载系统设置失败: ' + getErrorMessage(e, ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const groups = useMemo(() => {
    const map = new Map<string, SettingsItem[]>()
    for (const s of settings) {
      map.set(s.group, [...(map.get(s.group) || []), s])
    }
    const order = (name: string) => {
      const index = GROUP_ORDER.indexOf(name)
      return index === -1 ? GROUP_ORDER.length : index
    }
    return [...map.entries()].sort((a, b) => order(a[0]) - order(b[0]))
  }, [settings])

  const groupTabs = useMemo(
    () => [
      { key: ALL_GROUP, label: `全部（${settings.length}）` },
      ...groups.map(([group, items]) => ({
        key: group,
        label: `${group}（${items.length}）`,
      })),
    ],
    [groups, settings.length],
  )

  const dirtySettings = useMemo(
    () =>
      settings.filter(
        (item) => JSON.stringify(values[item.key]) !== JSON.stringify(item.value),
      ),
    [settings, values],
  )

  const dirtyKeys = useMemo(
    () => new Set(dirtySettings.map((item) => item.key)),
    [dirtySettings],
  )

  const visibleGroups = useMemo(() => {
    const keyword = searchText.trim().toLowerCase()
    const targetGroups =
      activeGroup === ALL_GROUP ? groups : groups.filter(([group]) => group === activeGroup)

    return targetGroups
      .map(([group, items]) => {
        const filtered = items.filter((item) => {
          if (
            keyword &&
            ![item.label, item.description, item.key].some((text) =>
              String(text || '')
                .toLowerCase()
                .includes(keyword),
            )
          ) {
            return false
          }
          if (statusFilter === 'custom' && item.is_default) return false
          if (statusFilter === 'prompt' && item.kind !== 'text') return false
          if (statusFilter === 'param' && item.kind === 'text') return false
          return true
        })
        return [group, filtered] as const
      })
      .filter(([, items]) => items.length > 0)
  }, [activeGroup, groups, searchText, statusFilter])

  const saveItem = async (item: SettingsItem, value: unknown) => {
    setSavingKey(item.key)
    try {
      const res = await settingsApi.update([{ key: item.key, value }])
      syncSettings(res.settings)
      message.success(
        value === null ? `「${item.label}」已重置为默认` : `「${item.label}」已保存`,
      )
    } catch (e) {
      message.error('保存失败: ' + getErrorMessage(e, ''))
    } finally {
      setSavingKey(null)
    }
  }

  const saveAllDirty = async () => {
    if (dirtySettings.length === 0) return
    setSavingKey('__all__')
    try {
      const res = await settingsApi.update(
        dirtySettings.map((item) => ({ key: item.key, value: values[item.key] })),
      )
      syncSettings(res.settings)
      message.success(`已保存 ${dirtySettings.length} 项设置`)
    } catch (e) {
      message.error('批量保存失败: ' + getErrorMessage(e, ''))
    } finally {
      setSavingKey(null)
    }
  }

  const openOptimize = (item: SettingsItem) => {
    setOptimizeTarget(item)
    setOptimizeResult(null)
    setOptimizeDraft('')
    setOptimizeInstruction('')
  }

  const runOptimize = async () => {
    if (!optimizeTarget) return
    setOptimizing(true)
    setOptimizeResult(null)
    try {
      const res = await settingsApi.optimizePrompt(
        optimizeTarget.key,
        String(values[optimizeTarget.key] ?? ''),
        optimizeInstruction,
      )
      setOptimizeResult(res)
      setOptimizeDraft(res.suggested || '')
      if (res.error) message.warning(res.error)
    } catch (e) {
      message.error('优化失败: ' + getErrorMessage(e, ''))
    } finally {
      setOptimizing(false)
    }
  }

  const applyOptimized = async () => {
    if (!optimizeTarget || !optimizeDraft.trim()) return
    await saveItem(optimizeTarget, optimizeDraft)
    setOptimizeTarget(null)
    setOptimizeResult(null)
    setOptimizeInstruction('')
  }

  const expandGroup = (items: SettingsItem[]) => {
    setExpandedKeys((prev) =>
      Array.from(new Set([...prev, ...items.map((item) => item.key)])),
    )
  }

  const collapseGroup = (items: SettingsItem[]) => {
    const keys = new Set(items.map((item) => item.key))
    setExpandedKeys((prev) => prev.filter((key) => !keys.has(key)))
  }

  const renderEditor = (item: SettingsItem) => {
    const value = values[item.key]
    const isText = item.kind === 'text'
    const rows = item.key === 'rule_import.system'
      ? 18
      : item.key.startsWith('ocr.') || item.key.startsWith('rule_import.')
        ? 12
        : 8

    return (
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        {item.description && (
          <div className="settings-editor-description">{item.description}</div>
        )}

        {item.kind === 'text' && (
          <TextArea
            rows={rows}
            value={String(value ?? '')}
            onChange={(e) => setValues((cur) => ({ ...cur, [item.key]: e.target.value }))}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          />
        )}

        {item.kind === 'number' && (
          <InputNumber
            value={value as number}
            min={0}
            max={item.key === 'llm.confidence_threshold' ? 1 : undefined}
            step={item.key === 'llm.confidence_threshold' ? 0.05 : 0.1}
            onChange={(next) => setValues((cur) => ({ ...cur, [item.key]: next }))}
            style={{ width: 200 }}
          />
        )}

        {item.kind === 'boolean' && (
          <Switch
            checked={Boolean(value)}
            checkedChildren="开"
            unCheckedChildren="关"
            onChange={(next) => setValues((cur) => ({ ...cur, [item.key]: next }))}
          />
        )}

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          {isText ? (
            <Button
              icon={<ThunderboltOutlined />}
              loading={optimizing && optimizeTarget?.key === item.key}
              onClick={() => openOptimize(item)}
            >
              LLM 优化
            </Button>
          ) : (
            <span />
          )}

          <Space>
            <Tooltip
              title={
                item.is_default
                  ? '当前为内置默认值，无需重置'
                  : '恢复为内置默认提示词/参数'
              }
            >
              <Button
                icon={<UndoOutlined />}
                disabled={item.is_default || savingKey === item.key}
                onClick={() => saveItem(item, null)}
              >
                重置默认
              </Button>
            </Tooltip>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={savingKey === item.key}
              disabled={!dirtyKeys.has(item.key)}
              onClick={() => saveItem(item, values[item.key])}
            >
              保存
            </Button>
          </Space>
        </div>
      </Space>
    )
  }

  const renderGroup = ([group, items]: readonly [string, SettingsItem[]]) => {
    const customCount = items.filter((item) => !item.is_default).length

    return (
      <Card
        key={group}
        size="small"
        className="settings-group-card"
        title={
          <Space size={8}>
            <Text strong>{group}</Text>
            <Tag>{items.length}</Tag>
            {customCount > 0 && <Tag color="blue">{customCount} 项已自定义</Tag>}
          </Space>
        }
        extra={
          <Space size={4}>
            <Button size="small" type="text" onClick={() => expandGroup(items)}>
              展开本组
            </Button>
            <Button size="small" type="text" onClick={() => collapseGroup(items)}>
              收起本组
            </Button>
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        <Collapse
          ghost
          activeKey={expandedKeys}
          onChange={(keys) => setExpandedKeys(typeof keys === 'string' ? [keys] : keys)}
          items={items.map((item) => ({
            key: item.key,
            label: (
              <div className="settings-item-label">
                <div className="settings-item-label-main">
                  <Text strong>{item.label}</Text>
                  {!item.is_default && <Tag color="blue">已自定义</Tag>}
                  {dirtyKeys.has(item.key) && <Tag color="orange">未保存</Tag>}
                </div>
                <div className="settings-item-label-meta">
                  <Text
                    type="secondary"
                    style={{
                      fontSize: 12,
                      maxWidth: 220,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {item.key}
                  </Text>
                  <Tag color={item.kind === 'text' ? 'purple' : 'geekblue'}>
                    {kindLabel(item)}
                  </Tag>
                </div>
              </div>
            ),
            children: renderEditor(item),
          }))}
        />
      </Card>
    )
  }

  const toolbar = (
    <Space wrap style={{ justifyContent: 'flex-end' }}>
      <Input
        allowClear
        prefix={<SearchOutlined />}
        placeholder="搜索设置名称、说明或键"
        value={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        style={{ width: 230 }}
      />
      <Segmented
        value={statusFilter}
        options={STATUS_OPTIONS}
        onChange={(value) => setStatusFilter(value as StatusFilter)}
      />
    </Space>
  )

  const tabContent =
    visibleGroups.length === 0 ? (
      <Empty
        description="没有匹配的设置项"
        style={{ paddingTop: 48, paddingBottom: 48 }}
      />
    ) : (
      <div>{visibleGroups.map(renderGroup)}</div>
    )

  return (
    <div>
      <PageHeader
        title="系统设置"
        subtitle="按业务分组管理提示词与运行参数；设置项默认收起，展开后可编辑、重置或使用 LLM 优化。"
        icon={<SettingOutlined />}
        bottomGap={12}
        extra={
          <Space>
            <Badge count={dirtySettings.length} size="small">
              <Button
                type="primary"
                icon={<SaveOutlined />}
                disabled={dirtySettings.length === 0}
                loading={savingKey === '__all__'}
                onClick={saveAllDirty}
              >
                保存全部修改
              </Button>
            </Badge>
            <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      />

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" tip="加载设置中...">
            <div style={{ padding: 40 }} />
          </Spin>
        </div>
      ) : settings.length === 0 ? (
        <Empty description="暂无设置项" />
      ) : (
        <>
          <Tabs
            activeKey={activeGroup}
            onChange={setActiveGroup}
            items={groupTabs.map((tab) => ({ key: tab.key, label: tab.label }))}
            tabBarExtraContent={toolbar}
          />
          {tabContent}
        </>
      )}

      <Modal
        title={`LLM 优化提示词 - ${optimizeTarget?.label || ''}`}
        open={Boolean(optimizeTarget)}
        onCancel={() => {
          setOptimizeTarget(null)
          setOptimizeResult(null)
          setOptimizeInstruction('')
        }}
        width={860}
        footer={
          optimizeResult ? (
            <Space>
              <Button
                onClick={() => {
                  setOptimizeTarget(null)
                  setOptimizeResult(null)
                  setOptimizeInstruction('')
                }}
              >
                放弃
              </Button>
              <Button
                type="primary"
                disabled={!optimizeDraft.trim()}
                onClick={applyOptimized}
              >
                应用该版本
              </Button>
            </Space>
          ) : (
            <Space>
              <Button onClick={() => setOptimizeTarget(null)}>取消</Button>
              <Button type="primary" loading={optimizing} onClick={runOptimize}>
                开始优化
              </Button>
            </Space>
          )
        }
      >
        {optimizeTarget && !optimizeResult && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="优化不会直接覆盖"
              description="LLM 会基于当前提示词生成建议版本与改动说明，确认后再应用。占位符（{xxx}）会被要求保持不变。"
            />
            <div>
              <Text type="secondary">优化要求（可选）：</Text>
              <TextArea
                rows={2}
                value={optimizeInstruction}
                onChange={(e) => setOptimizeInstruction(e.target.value)}
                placeholder="例如：强调多行明细必须汇总、输出必须严格 JSON…"
              />
            </div>
          </Space>
        )}
        {optimizeResult && (
          <Space direction="vertical" style={{ width: '100%' }}>
            {optimizeResult.error && (
              <Alert type="error" showIcon message={optimizeResult.error} />
            )}
            {optimizeResult.reasoning && (
              <div>
                <Text strong>改动说明：</Text>
                <Paragraph style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>
                  {optimizeResult.reasoning}
                </Paragraph>
              </div>
            )}
            <div>
              <Text strong>优化后的提示词（可再编辑）：</Text>
              <TextArea
                rows={14}
                value={optimizeDraft}
                onChange={(e) => setOptimizeDraft(e.target.value)}
                style={{ fontFamily: 'monospace', fontSize: 12, marginTop: 4 }}
              />
            </div>
          </Space>
        )}
      </Modal>
    </div>
  )
}
