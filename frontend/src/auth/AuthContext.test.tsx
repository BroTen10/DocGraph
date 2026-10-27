import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { authApi } from '../api/client'
import type { AuthSessionResponse } from '../types'
import {
  AuthProvider,
  useAuth,
  type AuthContextValue,
} from './AuthContext'

function Probe() {
  const { loading, initializationError, user, role, permissions, logout, refresh } = useAuth()
  return (
    <div>
      <div>{loading ? 'loading' : user?.name ?? 'anonymous'}</div>
      <div>{role ?? 'no-role'}</div>
      <div>{permissions.size}</div>
      <div>{initializationError ?? 'no-init-error'}</div>
      <button onClick={() => void logout()}>logout</button>
      <button onClick={() => void refresh()}>refresh</button>
    </div>
  )
}

const SESSION: AuthSessionResponse = {
  message: 'ok',
  csrf_token: 'csrf-1',
  user: {
    id: 'u1',
    name: '张三',
    email: 'zhang@example.com',
    phone_masked: '138****8000',
    role: 'tenant_admin',
  },
  tenant: { id: 't1', name: '甲公司' },
  role: 'tenant_admin',
  permissions: ['profile.read', 'settings.read'],
}

function seededAuth(overrides: Partial<AuthContextValue> = {}): AuthContextValue {
  return {
    loading: false,
    initializationError: null,
    user: SESSION.user,
    tenant: SESSION.tenant,
    role: SESSION.role,
    permissions: new Set(SESSION.permissions),
    login: async () => SESSION.role,
    logout: async () => undefined,
    refresh: async () => undefined,
    hasPermission: () => true,
    ...overrides,
  }
}

function renderProvider(initialAuth?: AuthContextValue | null) {
  return render(
    <MemoryRouter initialEntries={['/upload']}>
      <AuthProvider initialAuth={initialAuth}>
        <Probe />
      </AuthProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AuthProvider', () => {
  it('loads current user and csrf token', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(SESSION)
    renderProvider()
    expect(await screen.findByText('张三')).toBeInTheDocument()
    expect(screen.getByText('tenant_admin')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('clears the session when the API emits session-expired', async () => {
    renderProvider(seededAuth())
    expect(screen.getByText('张三')).toBeInTheDocument()
    act(() => {
      window.dispatchEvent(new CustomEvent('docgraph:session-expired'))
    })
    await waitFor(() => expect(screen.getByText('anonymous')).toBeInTheDocument())
    expect(screen.getByText('no-role')).toBeInTheDocument()
  })

  it('clears local state after logout even when cookie revocation is pending', async () => {
    vi.spyOn(authApi, 'logout').mockResolvedValue(undefined)
    renderProvider(seededAuth())
    await userEvent.click(screen.getByRole('button', { name: 'logout' }))
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expect(authApi.logout).toHaveBeenCalledOnce()
  })

  it('preserves the current user when refresh hits a temporary server error', async () => {
    const me = vi.spyOn(authApi, 'me').mockResolvedValue(SESSION)
    renderProvider()
    expect(await screen.findByText('张三')).toBeInTheDocument()

    me.mockRejectedValue({
      message: '服务暂时不可用',
      response: { status: 500, data: { detail: '服务暂时不可用' } },
    })
    await userEvent.click(screen.getByRole('button', { name: 'refresh' }))

    expect(await screen.findByText('服务暂时不可用')).toBeInTheDocument()
    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText('tenant_admin')).toBeInTheDocument()
  })

  it('only treats an unauthorized initialization response as anonymous', async () => {
    vi.spyOn(authApi, 'me').mockRejectedValue({
      message: '登录已失效',
      response: { status: 401, data: { detail: '登录已失效' } },
    })
    renderProvider()
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expect(screen.getByText('no-init-error')).toBeInTheDocument()
  })
})
