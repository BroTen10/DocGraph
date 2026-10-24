import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { platformApi } from '../../api/client'
import RegistrationReviewPage from './RegistrationReviewPage'
import TenantListPage from './TenantListPage'

const registration = {
  id: 'r1',
  organization_name: '测试贸易公司',
  admin_name: '张三',
  email: 'zhang@example.com',
  phone_masked: '138****8000',
  status: 'pending_review',
  email_verified_at: '2026-10-24T01:00:00Z',
  terms_accepted_at: '2026-10-24T00:00:00Z',
  review_note: null,
  rejection_reason: null,
  created_at: '2026-10-24T00:00:00Z',
}

const tenant = {
  id: 't1',
  name: '测试贸易公司',
  status: 'active',
  approved_at: '2026-10-24T02:00:00Z',
  created_at: '2026-10-24T00:00:00Z',
  member_count: 3,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RegistrationReviewPage', () => {
  it('requires a rejection reason', async () => {
    vi.spyOn(platformApi, 'listRegistrations').mockResolvedValue({
      registrations: [registration],
      total: 1,
    })
    const reject = vi.spyOn(platformApi, 'rejectRegistration')

    render(<RegistrationReviewPage />)

    expect(await screen.findByText('测试贸易公司')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '驳回' }))
    await userEvent.click(screen.getByRole('button', { name: '确认驳回' }))

    expect(await screen.findByText('请填写驳回原因')).toBeInTheDocument()
    expect(reject).not.toHaveBeenCalled()
  })

  it('approves a verified registration with an optional note', async () => {
    vi.spyOn(platformApi, 'listRegistrations')
      .mockResolvedValueOnce({ registrations: [registration], total: 1 })
      .mockResolvedValueOnce({ registrations: [], total: 0 })
    const approve = vi.spyOn(platformApi, 'approveRegistration').mockResolvedValue({
      message: '租户已开通',
      tenant_id: 't1',
      tenant_status: 'active',
      admin_role: 'tenant_admin',
      status: 'approved',
    })

    render(<RegistrationReviewPage />)

    await screen.findByText('测试贸易公司')
    await userEvent.click(screen.getByRole('button', { name: '批准' }))
    await userEvent.type(screen.getByPlaceholderText('可填写审核备注'), '资料完整')
    await userEvent.click(screen.getByRole('button', { name: '确认批准' }))

    await waitFor(() => {
      expect(approve).toHaveBeenCalledWith('r1', '资料完整')
    })
  })

  it('shows reject API errors without losing the dialog', async () => {
    vi.spyOn(platformApi, 'listRegistrations').mockResolvedValue({
      registrations: [registration],
      total: 1,
    })
    vi.spyOn(platformApi, 'rejectRegistration').mockRejectedValue({
      response: {
        data: { code: 'EMAIL_DELIVERY_FAILED', message: '审核结果邮件发送失败，请稍后重试' },
      },
    })

    render(<RegistrationReviewPage />)

    await screen.findByText('测试贸易公司')
    await userEvent.click(screen.getByRole('button', { name: '驳回' }))
    await userEvent.type(screen.getByPlaceholderText('请填写驳回原因'), '资料无法核验')
    await userEvent.click(screen.getByRole('button', { name: '确认驳回' }))

    expect(await screen.findByText('审核结果邮件发送失败，请稍后重试')).toBeInTheDocument()
  })
})

describe('TenantListPage', () => {
  it('shows metadata only and confirms suspension', async () => {
    vi.spyOn(platformApi, 'listTenants').mockResolvedValue({
      tenants: [tenant],
      total: 1,
    })
    const update = vi
      .spyOn(platformApi, 'setTenantStatus')
      .mockResolvedValue({ id: 't1', status: 'suspended' })

    render(<TenantListPage />)

    expect(await screen.findByText('测试贸易公司')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.queryByText('合同')).not.toBeInTheDocument()
    expect(screen.queryByText('审查结果')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '暂停' }))
    expect(screen.getByText(/现有成员会话将立即失效/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认暂停' }))

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith('t1', 'suspended')
    })
  })

  it('confirms archive and explains it cannot be undone from this page', async () => {
    vi.spyOn(platformApi, 'listTenants').mockResolvedValue({
      tenants: [tenant],
      total: 1,
    })
    const update = vi
      .spyOn(platformApi, 'setTenantStatus')
      .mockResolvedValue({ id: 't1', status: 'archived' })

    render(<TenantListPage />)

    await screen.findByText('测试贸易公司')
    await userEvent.click(screen.getByRole('button', { name: '归档' }))
    expect(screen.getByText(/归档后租户成员将无法进入系统/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认归档' }))

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith('t1', 'archived')
    })
  })
})
