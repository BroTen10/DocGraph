/**
 * 原件切换（上一张 / 下一张）控件回归测试。
 *
 * 业务约束：可切换的文档清单由调用方按业务边界给出（某条判定涉及的原件、
 * 某次上传合同下的文件列表），控件只在这个清单内前后移动，
 * 不会自己去找范围之外的文档。
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DocNavButtons from './DocNavButtons'

const docs = [
  { id: 'doc-1', file_name: 'BX-2026-0312_费用报销单.docx' },
  { id: 'doc-2', file_name: 'BX-2026-0312_增值税发票.docx' },
  { id: 'doc-3', file_name: 'BX-2026-0312_行程单.docx' },
]

const navButton = (label: '上一张' | '下一张') => {
  const btn = screen.getByText(label).closest('button')
  expect(btn).not.toBeNull()
  return btn as HTMLButtonElement
}

describe('DocNavButtons', () => {
  it('在给定清单内切换，并在首尾禁用对应按钮', async () => {
    const onNavigate = vi.fn()
    render(<DocNavButtons docs={docs} currentId="doc-1" onNavigate={onNavigate} scopeLabel="本检查项涉及文件" />)

    expect(navButton('上一张')).toBeDisabled()
    expect(navButton('下一张')).toBeEnabled()
    expect(screen.getByText('1/3')).toBeInTheDocument()

    await userEvent.click(navButton('下一张'))
    expect(onNavigate).toHaveBeenCalledWith('doc-2')
  })

  it('最后一张禁用「下一张」，点击不会切到清单外', async () => {
    const onNavigate = vi.fn()
    render(<DocNavButtons docs={docs} currentId="doc-3" onNavigate={onNavigate} />)

    const next = navButton('下一张')
    expect(next).toBeDisabled()
    expect(navButton('上一张')).toBeEnabled()
    expect(screen.getByText('3/3')).toBeInTheDocument()

    await userEvent.click(next)
    expect(onNavigate).not.toHaveBeenCalled()

    await userEvent.click(navButton('上一张'))
    expect(onNavigate).toHaveBeenCalledWith('doc-2')
  })

  it('清单只有一份文档时不渲染切换控件', () => {
    render(
      <DocNavButtons
        docs={[{ id: 'doc-1', file_name: 'BX-2026-0312_费用报销单.docx' }]}
        currentId="doc-1"
        onNavigate={vi.fn()}
      />,
    )
    expect(screen.queryByLabelText('上一张文档')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('下一张文档')).not.toBeInTheDocument()
  })

  it('当前文档不在清单内（或未选中）时不渲染，避免越界切换', () => {
    const { rerender } = render(
      <DocNavButtons docs={docs} currentId="doc-outside" onNavigate={vi.fn()} />,
    )
    expect(screen.queryByLabelText('下一张文档')).not.toBeInTheDocument()

    rerender(<DocNavButtons docs={docs} currentId={null} onNavigate={vi.fn()} />)
    expect(screen.queryByLabelText('下一张文档')).not.toBeInTheDocument()
  })
})
