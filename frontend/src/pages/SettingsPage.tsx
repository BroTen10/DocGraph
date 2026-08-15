import { useEffect, useMemo, useState } from 'react'
import {
  Card, Row, Col, Spin, Empty, message, Typography, Button, Input, InputNumber, Switch,
  Space, Tag, Modal, Alert, Tooltip,
} from 'antd'
import {
  SaveOutlined, UndoOutlined, ThunderboltOutlined, SettingOutlined,
} from '@ant-design/icons'
import { settingsApi, getErrorMessage } from '../api/client'
import type { SettingsItem, PromptOptimizeResult } from '../types'
import PageHeader from '../components/PageHeader'

const { Text, Paragraph } = Typography
const { TextArea } = Input

const GROUP_ORDER = ['OCR 识别', '规则解析', '审查与建议', '文档类型', '图谱', '运行参数']

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

  const load = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.list()
      setSettings(res.settings)
      setValues(Object.fromEntries(res.settings.map((s) => [s.key, s.value])))
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
    return [...map.entries()].sort(
      (a, b) => GROUP_ORDER.indexOf(a[0]) - GROUP_ORDER.indexOf(b[0]),
    )
  }, [settings])

  const saveItem = async (item: SettingsItem, value: unknown) => {
    setSavingKey(item.key)
    try {
      const res = await settingsApi.update([{ key: item.key, value }])
      setSettings(res.settings)
      setValues(Object.fromEntries(res.settings.map((s) => [s.key, s.value])))
      message.success(value === null ? `「${item.label}」已重置为默认` : `「${item.label}」已保存`)
    } catch (e) {
      message.error('保存失败: ' + getErrorMessage(e, ''))
    } finally {
      setSavingKey(null)
    }
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

  const renderItem = (item: SettingsItem) => {
    const value = values[item.key]
    const saved = settings.find((s) => s.key === item.key)?.value
    const dirty = JSON.stringify(value) !== JSON.stringify(saved)
    return (
      <Card
        key={item.key}
        size="small"
        title={
          <Space size={6}>
            <Text strong>{item.label}</Text>
            {!item.is_default && <Tag color="blue">已自定义</Tag>}
          </Space>
        }
        extra={
          item.kind === 'text' ? (
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              loading={optimizing && optimizeTarget?.key === item.key}
              onClick={() => {
                setOptimizeTarget(item)
                setOptimizeResult(null)
                setOptimizeDraft('')
                setOptimizeInstruction('')
              }}
            >
              LLM 优化
            </Button>
          ) : null
        }
        style={{ marginBottom: 12 }}
      >
        {item.description && (
          <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 0 }}>
            {item.description}
          </Paragraph>
        )}
        <Space direction="vertical" style={{ width: '100%' }}>
          {item.kind === 'text' && (
            <TextArea
              rows={item.key.startsWith('ocr.') || item.key.startsWith('rule_import.') ? 8 : 5}
              value={String(value ?? '')}
              onChange={(e) => setValues((v) => ({ ...v, [item.key]: e.target.value }))}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
          )}
          {item.kind === 'number' && (
            <InputNumber
              value={value as number}
              min={0}
              onChange={(v) => setValues((cur) => ({ ...cur, [item.key]: v }))}
              style={{ width: 200 }}
            />
          )}
          {item.kind === 'boolean' && (
            <Switch
              checked={Boolean(value)}
              checkedChildren="开"
              unCheckedChildren="关"
              onChange={(v) => setValues((cur) => ({ ...cur, [item.key]: v }))}
            />
          )}
          <Space>
            <Button
              type="primary"
              size="small"
              icon={<SaveOutlined />}
              loading={savingKey === item.key}
              disabled={!dirty}
              onClick={() => saveItem(item, values[item.key])}
            >
              保存
            </Button>
            <Tooltip title={item.is_default ? '当前为内置默认值，无需重置' : '恢复为内置默认提示词/参数'}>
              <Button
                size="small"
                icon={<UndoOutlined />}
                disabled={item.is_default || savingKey === item.key}
                onClick={() => saveItem(item, null)}
              >
                重置默认
              </Button>
            </Tooltip>
          </Space>
        </Space>
      </Card>
    )
  }

  return (
    <div>
      <PageHeader
        title="系统设置"
        subtitle="提示词与运行参数的集中管理；提示词可查看、修改，并可用 LLM 自动优化"
        icon={<SettingOutlined />}
        extra={
          <Button icon={<SaveOutlined />} onClick={load} loading={loading}>
            刷新
          </Button>
        }
      />

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" tip="加载设置中..."><div style={{ padding: 40 }} /></Spin>
        </div>
      ) : groups.length === 0 ? (
        <Empty description="暂无设置项" />
      ) : (
        <Row gutter={[16, 16]}>
          {groups.map(([group, items]) => (
            <Col span={12} key={group}>
              <Card
                size="small"
                title={
                  <Space>
                    <Text strong>{group}</Text>
                    <Tag>{items.length}</Tag>
                  </Space>
                }
              >
                {items.map(renderItem)}
              </Card>
            </Col>
          ))}
        </Row>
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
              <Button type="primary" disabled={!optimizeDraft.trim()} onClick={applyOptimized}>
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
            {optimizeResult.error && <Alert type="error" showIcon message={optimizeResult.error} />}
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
