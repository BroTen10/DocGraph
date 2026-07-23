import { useState, useEffect, useRef } from 'react'
import {
  Card, Row, Col, Button, Select, message, Typography, Progress, Empty,
  Tag, List, Space, Statistic, Alert,
} from 'antd'
import { PlayCircleOutlined, ReloadOutlined, FileSearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { contractsApi, reviewsApi } from '../api/client'
import type { ContractBrief, ReviewTaskSummary } from '../types'
import dayjs from 'dayjs'

const { Title, Text } = Typography

export default function ReviewPage() {
  const navigate = useNavigate()
  const [contracts, setContracts] = useState<ContractBrief[]>([])
  const [selectedContract, setSelectedContract] = useState<string | undefined>()
  const [running, setRunning] = useState(false)
  const [task, setTask] = useState<ReviewTaskSummary | null>(null)
  const [history, setHistory] = useState<ReviewTaskSummary[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = async () => {
    try {
      setContracts(await contractsApi.list())
    } catch (e: any) {
      message.error('加载合同失败: ' + (e?.message || e))
    }
  }

  useEffect(() => {
    load()
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const startReview = async () => {
    if (!selectedContract) {
      message.warning('请选择合同')
      return
    }
    setRunning(true)
    try {
      const t = await reviewsApi.start(selectedContract)
      setTask(t)
      // 轮询进度
      pollRef.current = setInterval(async () => {
        try {
          const cur = await reviewsApi.getStatus(t.id)
          setTask(cur)
          if (cur.status === 'completed' || cur.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current)
            pollRef.current = null
            setRunning(false)
            setHistory((h) => [cur, ...h])
            if (cur.status === 'completed') {
              message.success('审查完成')
            } else {
              message.error('审查失败: ' + (cur.error || ''))
            }
          }
        } catch (e) {
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          setRunning(false)
        }
      }, 2000)
    } catch (e: any) {
      message.error('启动审查失败: ' + (e?.response?.data?.detail || e?.message || e))
      setRunning(false)
    }
  }

  const stageLabel: Record<string, string> = {
    '初始化': '初始化',
    'OCR 与字段提取中': 'OCR 与字段提取中',
    '规则比对中': '规则比对中',
    '生成报告中': '生成报告中',
    '完成': '完成',
  }

  const statusColor: Record<string, string> = {
    pending: 'default',
    running: 'processing',
    completed: 'success',
    failed: 'error',
  }

  return (
    <div>
      <Row justify="space-between" align="middle">
        <Col><Title level={4}>审查执行</Title></Col>
        <Col>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新合同</Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={startReview}
              loading={running}
              disabled={!selectedContract}
            >
              开始审查
            </Button>
          </Space>
        </Col>
      </Row>

      <Card style={{ marginTop: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={12}>
            <Text type="secondary">选择合同：</Text>
            <Select
              style={{ width: '100%', marginTop: 4 }}
              placeholder="请选择已上传的合同"
              value={selectedContract}
              onChange={setSelectedContract}
              options={contracts.map((c) => ({
                value: c.id,
                label: `${c.contract_no}（${c.file_count} 个文件，${dayjs(c.upload_time).format('YYYY-MM-DD HH:mm')}）`,
              }))}
            />
          </Col>
          <Col span={12}>
            {task && (
              <Space size="large">
                <Statistic title="状态" valueRender={() => <Tag color={statusColor[task.status]}>{task.status}</Tag>} />
                <Statistic title="进度" value={`${task.progress}%`} />
                <Statistic title="阶段" value={task.stage || '-'} />
              </Space>
            )}
          </Col>
        </Row>

        {task && (
          <div style={{ marginTop: 24 }}>
            <Alert
              type={task.status === 'failed' ? 'error' : task.status === 'completed' ? 'success' : 'info'}
              showIcon
              message={task.stage || ''}
              description={task.error ? `错误: ${task.error}` : undefined}
            />
            <Progress
              percent={task.progress}
              status={task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : 'active'}
              style={{ marginTop: 16 }}
            />
            {task.status === 'completed' && task.summary && (
              <Row gutter={16} style={{ marginTop: 16 }}>
                <Col span={6}><Statistic title="总检查项" value={task.summary.total || 0} /></Col>
                <Col span={6}><Statistic title="通过" value={task.summary.pass || 0} valueStyle={{ color: '#52c41a' }} /></Col>
                <Col span={6}><Statistic title="不通过" value={task.summary.fail || 0} valueStyle={{ color: '#ff4d4f' }} /></Col>
                <Col span={6}><Statistic title="无法核验" value={task.summary.unverifiable || 0} valueStyle={{ color: '#faad14' }} /></Col>
              </Row>
            )}
            {task.status === 'completed' && (
              <Button
                type="primary"
                icon={<FileSearchOutlined />}
                style={{ marginTop: 16 }}
                onClick={() => navigate(`/results?task_id=${task.id}`)}
              >
                查看结果详情
              </Button>
            )}
          </div>
        )}
      </Card>

      <Card title="审查历史" style={{ marginTop: 16 }}>
        {history.length === 0 ? (
          <Empty description="暂无审查历史" />
        ) : (
          <List
            dataSource={history}
            renderItem={(t) => (
              <List.Item
                actions={[
                  <Button size="small" onClick={() => navigate(`/results?task_id=${t.id}`)}>查看结果</Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Tag color={statusColor[t.status]}>{t.status}</Tag>
                      <Text>{dayjs(t.start_time).format('YYYY-MM-DD HH:mm:ss')}</Text>
                      {t.end_time && <Text type="secondary">- {dayjs(t.end_time).format('HH:mm:ss')}</Text>}
                    </Space>
                  }
                  description={
                    t.summary ? `通过 ${t.summary.pass || 0} / 不通过 ${t.summary.fail || 0} / 无法核验 ${t.summary.unverifiable || 0}` : t.stage || ''
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  )
}
