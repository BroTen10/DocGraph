import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { settingsApi, tenantMembersApi, versionApi } from '../../api/client'
import MembersTab from './MembersTab'
import SettingsPage from '../SettingsPage'

const member = {
  id: 'm1',
  name: '王五',
  email: 'wang@example.com',
  phone_masked: '139****9000',
  role: 'tenant_admin' as const,
  status: 'active' as const,
  joined_at: '2026-10-24T00:00:00Z',
  created_at: '2026-10-24T00:00:00Z',
  updated_at: '2026-10-24T00:00:00Z',
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MembersTab', () => {
  it('shows masked phone and the localized role', async () => {
    vi.spyOn(tenantMembersApi, 'list').mockResolvedValue({
      members: [member],
      invitations: [],
    })

    render(<MembersTab />)

    expect(await screen.findByText('139****9000')).toBeInTheDocument()
    expect(screen.getByText('租户管理员')).toBeInTheDocument()
  })

  it('shows the specific last-tenant-admin error', async () => {
    vi.spyOn(tenantMembersApi, 'list').mockResolvedValue({
      members: [member],
      invitations: [],
    })
    vi.spyOn(tenantMembersApi, 'update').mockRejectedValue({
      response: {
        status: 409,
        data: { code: 'LAST_TENANT_ADMIN', message: '至少需要保留一名有效的租户管理员' },
      },
    })

    render(<MembersTab />)

    await screen.findByText('王五')
    await userEvent.click(screen.getByRole('combobox', { name: '角色-王五' }))
    await userEvent.click(await screen.findByText('审查员'))

    expect(
      await screen.findByText('至少需要保留一名有效的租户管理员'),
    ).toBeInTheDocument()
  })

  it('reveals a full phone only after an audited confirmation', async () => {
    vi.spyOn(tenantMembersApi, 'list').mockResolvedValue({
      members: [member],
      invitations: [],
    })
    const reveal = vi
      .spyOn(tenantMembersApi, 'revealPhone')
      .mockResolvedValue({ phone: '+8613900139000' })

    render(<MembersTab />)

    await screen.findByText('王五')
    await userEvent.click(screen.getByRole('button', { name: /查看完整手机号/ }))
    expect(screen.getByText(/将记录操作审计/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认查看' }))

    expect(await screen.findByText('+8613900139000')).toBeInTheDocument()
    expect(reveal).toHaveBeenCalledWith('m1')
  })
})

describe('SettingsPage', () => {
  it('provides runtime and member tabs', async () => {
    vi.spyOn(settingsApi, 'list').mockResolvedValue({ settings: [] })
    vi.spyOn(versionApi, 'get').mockResolvedValue({
      kind: 'backend',
      name: 'DocGraph',
      version: '0.0.0-test',
      label: '0.0.0-test',
      describe: null,
      tag: null,
      commit: null,
      branch: 'test',
      dirty: false,
      build_env: 'test',
      built_at: '2026-10-24T00:00:00Z',
      source: 'build-args',
    })
    vi.spyOn(tenantMembersApi, 'list').mockResolvedValue({
      members: [],
      invitations: [],
    })

    render(<SettingsPage />)

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '运行设置' })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: '用户与权限' })).toBeInTheDocument()
    })
  })
})
