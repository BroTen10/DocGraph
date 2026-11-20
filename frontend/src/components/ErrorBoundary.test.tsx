/**
 * ErrorBoundary 测试
 *
 * 渲染期异常必须降级成可读提示，而不是让 React 卸载整棵树（整页白屏）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ErrorBoundary from './ErrorBoundary'

/** 开关式抛错：置为 false 后再渲染即恢复正常（用于验证「重试」与 resetKey） */
let shouldThrow = false

function Flaky({ label = '正常内容' }: { label?: string }) {
  if (shouldThrow) {
    throw new Error('模拟渲染异常')
  }
  return <div>{label}</div>
}

function AlwaysBroken(): never {
  throw new Error('mammoth 注入失败')
}

describe('ErrorBoundary', () => {
  let consoleError: ReturnType<typeof vi.spyOn>
  // React 在 dev 下用「合成事件」重放渲染异常以保留堆栈，jsdom 会把这段
  // 预期内的异常当未捕获错误打到 stderr，这里拦掉以免淹没测试输出
  const swallowJsdomError = (event: ErrorEvent) => event.preventDefault()

  beforeEach(() => {
    shouldThrow = false
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    window.addEventListener('error', swallowJsdomError)
  })

  afterEach(() => {
    consoleError.mockRestore()
    window.removeEventListener('error', swallowJsdomError)
  })

  it('正常情况下原样渲染子内容', () => {
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    )
    expect(screen.getByText('正常内容')).toBeInTheDocument()
    expect(screen.queryByText('页面出错了')).not.toBeInTheDocument()
  })

  it('子组件抛错时显示错误提示，且边界外的内容不受影响', () => {
    render(
      <div>
        <div>边界外内容</div>
        <ErrorBoundary resetKey="doc-1">
          <AlwaysBroken />
        </ErrorBoundary>
      </div>,
    )

    expect(screen.getByText('页面出错了')).toBeInTheDocument()
    expect(screen.getByText(/mammoth 注入失败/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重\s*试/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新页面' })).toBeInTheDocument()
    // 边界只吃掉自己子树里的异常
    expect(screen.getByText('边界外内容')).toBeInTheDocument()
  })

  it('支持自定义标题与说明', () => {
    render(
      <ErrorBoundary title="文档对照视图加载失败" description="请关闭抽屉后重试">
        <AlwaysBroken />
      </ErrorBoundary>,
    )
    expect(screen.getByText('文档对照视图加载失败')).toBeInTheDocument()
    expect(screen.getByText('请关闭抽屉后重试')).toBeInTheDocument()
  })

  it('点「重试」后重新挂载子树，恢复正常内容', async () => {
    shouldThrow = true
    render(
      <ErrorBoundary>
        <Flaky label="恢复后的内容" />
      </ErrorBoundary>,
    )
    expect(screen.getByText('页面出错了')).toBeInTheDocument()

    shouldThrow = false
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))

    expect(await screen.findByText('恢复后的内容')).toBeInTheDocument()
    expect(screen.queryByText('页面出错了')).not.toBeInTheDocument()
  })

  it('resetKey 变化时自动清除错误（例如切换到另一份文档）', () => {
    shouldThrow = true
    const { rerender } = render(
      <ErrorBoundary resetKey="doc-1">
        <Flaky />
      </ErrorBoundary>,
    )
    expect(screen.getByText('页面出错了')).toBeInTheDocument()

    shouldThrow = false
    rerender(
      <ErrorBoundary resetKey="doc-2">
        <Flaky label="第二份文档" />
      </ErrorBoundary>,
    )

    expect(screen.getByText('第二份文档')).toBeInTheDocument()
    expect(screen.queryByText('页面出错了')).not.toBeInTheDocument()
  })

  it('onError 回调收到原始错误', () => {
    const onError = vi.fn()
    render(
      <ErrorBoundary onError={onError}>
        <AlwaysBroken />
      </ErrorBoundary>,
    )
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0]).toBeInstanceOf(Error)
    expect((onError.mock.calls[0][0] as Error).message).toBe('mammoth 注入失败')
  })
})
