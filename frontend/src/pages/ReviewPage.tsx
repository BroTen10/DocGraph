import { useState, useEffect, useRef } from 'react'
import {
  Card, Row, Col, Button, Select, message, Typography, Progress, Empty,
  Tag, List, Space, Statistic, Alert, Modal,
} from 'antd'
import { PlayCircleOutlined, ReloadOutlined, FileSearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { contractsApi, reviewsApi } from '../api/client'
import type { ContractBrief, ContractDetail, ReviewTaskSummary } from '../types'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { useRuleSet } from '../context/RuleSetContext'
import dayjs from 'dayjs'

const { Text } = Typography

export default function ReviewPage() {
  const navigate = useNavigate()
  const { currentId } = useRuleSet()
  const [contracts, setContracts] = useState<ContractBrief[]>([])
  const [selectedContract, setSelectedContract] = useState<string | undefined>()
  const [running, setRunning] = useState(false)
  const [task, setTask] = useState<ReviewTaskSummary | null>(null)
  const [history, setHistory] = useState<ReviewTaskSummary[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // 选中合同的详情(用于展示 OCR 完成度,启动审查前校验)
  const [contractDetail, setContractDetail] = useState<ContractDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = async () => {
    if (!currentId) return
    try {
      setContracts(await contractsApi.list(currentId))
    } catch (e: any) {
      message.error('加载合同失败: ' + (e?.message || e))
    }
  }

  useEffect(() => {
    load()
    loadHistory()
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [currentId])

  // 抽出轮询逻辑,供启动审查和续接历史 running 任务复用
  const startPolling = (taskId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const cur = await reviewsApi.getStatus(taskId)
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
  }

  // 加载历史审查任务;若有 running/pending 任务自动续接轮询(避免刷新页面后丢失进度)
  const loadHistory = async () => {
    if (!currentId) return
    try {
      const list = await reviewsApi.list(currentId, { limit: 50 })
      setHistory(list)
      const running = list.find((t) => t.status === 'running' || t.status === 'pending')
      if (running) {
        setTask(running)
        setRunning(true)
        startPolling(running.id)
      }
    } catch (e) {
      console.warn('加载审查历史失败:', e)
    }
  }

  // 选择合同时加载详情,展示 OCR 完成度
  const onSelectContract = async (id: string) => {
    setSelectedContract(id)
    setContractDetail(null)
    setTask(null)
    setDetailLoading(true)
    try {
      setContractDetail(await contractsApi.get(id))
    } catch (e) {
      console.warn('加载合同详情失败(OCR 校验将跳过):', e)
    } finally {
      setDetailLoading(false)
    }
  }

  const doStartReview = async () => {
    setRunning(true)
    try {
      const t = await reviewsApi.start(selectedContract!)
      setTask(t)
      startPolling(t.id)
    } catch (e: any) {
      message.error('启动审查失败: ' + (e?.response?.data?.detail || e?.message || e))
      setRunning(false)
    }
  }

  const startReview = async () => {
    if (!selectedContract) {
      message.warning('请选择合同')
      return
    }
    // 校验 OCR 完成度:有未识别文档时弹确认,避免用户启动无效审查
    if (contractDetail) {
      const docs = contractDetail.documents
      const notDone = docs.filter((d) => d.ocr_status !== 'done')
      if (notDone.length > 0) {
        Modal.confirm({
          title: '部分文档尚未完成 OCR',
          content: `该合同共 ${docs.length} 个文档,其中 ${notDone.length} 个未完成 OCR 识别。未识别文档的检查项将无法核验,是否继续?`,
          okText: '继续审查',
          cancelText: '取消',
          onOk: doStartReview,
        })
        return
      }
    }
    doStartReview()
  }

  const statusColor: Record<string, string> = {
    pending: 'default',
    running: 'processing',
    completed: 'success',
    failed: 'error',
  }

  return (
    <div>
      <PageHeader
        title="审查执行"
        subtitle="选择已上传的合同启动审查任务，系统将异步执行 OCR、字段提取、规则比对与报告生成"
        icon={<FileSearchOutlined />}
        extra={
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
        }
      />

      <Card style={{ marginTop: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={12}>
            <Text type="secondary">选择合同：</Text>
            <Select
              style={{ width: '100%', marginTop: 4 }}
              placeholder="请选择已上传的合同"
              value={selectedContract}
              onChange={onSelectContract}
              options={contracts.map((c) => ({
                value: c.id,
                label: `${c.contract_no}（${c.file_count} 个文件，${dayjs(c.upload_time).format('YYYY-MM-DD HH:mm')}）`,
              }))}
            />
            {detailLoading && <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8' }}>加载合同详情...</div>}
            {contractDetail && (
              <div style={{ marginTop: 8, fontSize: 12 }}>
                {(() => {
                  const docs = contractDetail.documents
                  const done = docs.filter((d) => d.ocr_status === 'done').length
                  const total = docs.length
                  const color = done === total ? 'green' : done === 0 ? 'red' : 'orange'
                  return <Tag color={color}>OCR 完成 {done}/{total}</Tag>
                })()}
              </div>
            )}
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
          <EmptyState description="暂无审查历史，启动一次审查后将在此显示" padding={48} />
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
