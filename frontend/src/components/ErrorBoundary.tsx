/**
 * 渲染错误边界
 *
 * React 的渲染 / 生命周期异常如果没有边界捕获，React 会卸载整棵组件树，
 * 表现为「整页白屏」——例如文档对照视图里 mammoth 注入 HTML 后，
 * React 删除已被覆盖的节点抛出 removeChild NotFoundError 时就是这样。
 *
 * 这个边界把异常降级成可读的错误提示，并提供「重试」与「刷新页面」，
 * 同时把错误信息展示出来，便于用户复制反馈。
 *
 * 注意：错误边界只能捕获渲染期异常（render / 生命周期 / 构造函数）。
 * 事件回调里抛出的错误、以及 Promise 拒绝（例如接口失败）不会被捕获，
 * 这些仍需要在各自的调用处处理。
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Button, Result, Space, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

const { Text, Paragraph } = Typography

export interface ErrorBoundaryProps {
  children: ReactNode
  /** 兜底提示标题 */
  title?: string
  /** 兜底提示说明（默认给一句通用说明） */
  description?: string
  /**
   * 上下文标识；变化时自动清除错误状态。
   * 例如 OCR 对照抽屉里切换文档后，不应继续显示上一份文档的错误。
   */
  resetKey?: unknown
  /** 出错回调（日志上报用） */
  onError?: (error: Error, info: ErrorInfo) => void
}

interface ErrorBoundaryState {
  error: Error | null
  componentStack: string | null
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, componentStack: null }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ componentStack: info.componentStack ?? null })
    // 保留完整堆栈便于定位；生产环境可再通过 onError 上报
    console.error('[ErrorBoundary] 渲染异常:', error, info.componentStack)
    this.props.onError?.(error, info)
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.reset()
    }
  }

  reset = () => {
    this.setState({ error: null, componentStack: null })
  }

  render() {
    const { error, componentStack } = this.state
    if (!error) return this.props.children

    const {
      title = '页面出错了',
      description = '页面渲染时发生异常，可点击「重试」；若持续出现，请把下方错误信息复制给管理员。',
    } = this.props

    const detail = [
      `${error.name || 'Error'}: ${error.message}`,
      componentStack ? `\n组件调用栈:${componentStack}` : '',
      error.stack ? `\n原始堆栈:\n${error.stack}` : '',
    ].join('')

    return (
      <Result
        status="error"
        title={title}
        subTitle={description}
        extra={
          <Space>
            <Button type="primary" icon={<ReloadOutlined />} onClick={this.reset}>
              重试
            </Button>
            <Button onClick={() => window.location.reload()}>刷新页面</Button>
          </Space>
        }
      >
        <Paragraph type="secondary" style={{ marginBottom: 6, fontSize: 12 }}>
          错误信息（可复制后反馈给管理员）：
        </Paragraph>
        <div
          style={{
            background: '#f8fafc',
            border: '1px solid #e2e8f0',
            borderRadius: 8,
            padding: '10px 12px',
            maxHeight: 200,
            overflow: 'auto',
          }}
        >
          <Text
            type="secondary"
            style={{
              fontSize: 12,
              fontFamily: 'monospace',
              whiteSpace: 'pre-wrap',
              // 堆栈里的 URL 没有空格，不强制断行会撑出横向滚动条
              overflowWrap: 'anywhere',
              wordBreak: 'break-word',
            }}
          >
            {detail}
          </Text>
        </div>
        <Paragraph
          copyable={{ text: detail, tooltips: ['复制错误详情', '已复制'] }}
          type="secondary"
          style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
        >
          复制错误详情
        </Paragraph>
      </Result>
    )
  }
}
