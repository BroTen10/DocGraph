import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { authApi } from './api/client'
import { AuthProvider } from './auth/AuthContext'
import type { AuthContextValue } from './auth/AuthContext'
import { AppRoutes } from './App'
import type { AuthSessionResponse, UserRole } from './types'

vi.mock('react-pdf', () => ({
  Document: () => null,
  Page: () => null,
  pdfjs: {
    GlobalWorkerOptions: {
      workerSrc: '',
    },
  },
}))

const apiMocks = vi.hoisted(() => ({
  ruleSetsList: vi.fn(),
  registrationsList: vi.fn(),
  tenantsList: vi.fn(),
}))

vi.mock('./api/client', async () => {
  const actual = await vi.importActual<typeof import('./api/client')>('./api/client')
  return {
    ...actual,
    ruleSetsApi: {
      ...actual.ruleSetsApi,
      list: apiMocks.ruleSetsList,
    },
    constantsApi: {
      ...actual.constantsApi,
      docTypes: vi.fn().mockResolvedValue({
        doc_types: [],
        check_categories: [],
      }),
    },
    contractsApi: {
      ...actual.contractsApi,
      list: vi.fn().mockResolvedValue([]),
    },
    platformApi: {
      ...actual.platformApi,
      listRegistrations: apiMocks.registrationsList,
      listTenants: apiMocks.tenantsList,
    },
    versionApi: {
      ...actual.versionApi,
      get: vi.fn().mockResolvedValue({
        kind: 'backend',
        label: 'test',
        version: '0.0.0',
        commit: null,
        dirty: false,
        build_env: 'test',
        source: 'unknown',
        built_at: null,
      }),
    },
  }
})

const ruleSet = {
  id: 'rs-1',
  name: '审查规则集',
  description: null,
  doc_types: [],
  check_categories: [],
  use_default_skill: true,
  is_default: true,
  created_at: '2026-10-24T00:00:00Z',
  updated_at: '2026-10-24T00:00:00Z',
}

function authFor(role: UserRole | null, permissions: string[] = []): AuthContextValue {
  return {
    loading: false,
    initializationError: null,
    user: role ? {
      id: 'u-1',
      name: '测试用户',
      email: 'user@example.com',
      phone_masked: '138****8000',
      role,
    } : null,
    tenant: role && role !== 'platform_admin' ? { id: 'tenant-1', name: '测试租户' } : null,
    role,
    permissions: new Set(permissions),
    login: async () => role,
    logout: async () => undefined,
    refresh: async () => undefined,
    hasPermission: (permission) => permissions.includes(permission),
  }
}

function renderApp(auth: AuthContextValue | null, path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider initialAuth={auth}>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  )
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

const TENANT_ADMIN_SESSION: AuthSessionResponse = {
  message: '登录成功',
  csrf_token: 'csrf-tenant',
  user: {
    id: 'u-tenant',
    name: '租户管理员',
    email: 'tenant-admin@example.com',
    phone_masked: '138****8000',
    role: 'tenant_admin',
  },
  tenant: { id: 'tenant-1', name: '测试租户' },
  role: 'tenant_admin',
  permissions: ['profile.read', 'review.execute'],
}

const PLATFORM_ADMIN_SESSION: AuthSessionResponse = {
  message: '登录成功',
  csrf_token: 'csrf-platform',
  user: {
    id: 'u-platform',
    name: '平台管理员',
    email: 'platform-admin@example.com',
    phone_masked: '139****9000',
    role: 'platform_admin',
  },
  tenant: null,
  role: 'platform_admin',
  permissions: ['profile.read', 'platform.registrations.read'],
}

function renderLogin(fromPath: string) {
  return render(
    <MemoryRouter
      initialEntries={[{
        pathname: '/login',
        state: { from: { pathname: fromPath } },
      }]}
    >
      <AuthProvider initialAuth={null}>
        <AppRoutes />
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  )
}

async function submitLogin() {
  await userEvent.type(
    screen.getByPlaceholderText('name@example.com'),
    'user@example.com',
  )
  await userEvent.type(screen.getByPlaceholderText('请输入密码'), 'DocGraph2026')
  await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))
}

describe('application shell routing', () => {
  beforeEach(() => {
    localStorage.clear()
    apiMocks.ruleSetsList.mockReset()
    apiMocks.ruleSetsList.mockResolvedValue([ruleSet])
    apiMocks.registrationsList.mockReset()
    apiMocks.registrationsList.mockResolvedValue({ registrations: [], total: 0 })
    apiMocks.tenantsList.mockReset()
    apiMocks.tenantsList.mockResolvedValue({ tenants: [], total: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('hides settings from reviewers and shows tenant workspace menus', async () => {
    renderApp(authFor('reviewer', ['review.view', 'review.execute']), '/upload')

    expect((await screen.findAllByText('文档上传')).length).toBeGreaterThan(0)
    expect(screen.queryByText('系统设置')).not.toBeInTheDocument()
    expect(screen.getByText('测试用户')).toBeInTheDocument()
    expect(screen.getByText('测试租户')).toBeInTheDocument()
  })

  it('shows settings to tenant administrators', async () => {
    renderApp(
      authFor('tenant_admin', ['review.view', 'review.execute', 'settings.read']),
      '/upload',
    )

    expect(await screen.findByText('系统设置')).toBeInTheDocument()
  })

  it('blocks direct settings URLs for reviewers', async () => {
    renderApp(authFor('reviewer', ['review.view', 'review.execute']), '/settings')

    expect(await screen.findByText('无权访问此页面')).toBeInTheDocument()
  })

  it('redirects anonymous users to login', async () => {
    renderApp(null, '/upload')

    expect(await screen.findByRole('heading', { name: '登录' })).toBeInTheDocument()
  })

  it('does not mount RuleSetProvider for platform administrators', async () => {
    renderApp(
      authFor('platform_admin', ['platform.registrations.read']),
      '/platform/registrations',
    )

    expect(await screen.findByText('注册审核', { selector: 'h3' })).toBeInTheDocument()
    expect(screen.queryByText('文档上传')).not.toBeInTheDocument()
    await waitFor(() => expect(apiMocks.ruleSetsList).not.toHaveBeenCalled())
  })

  it('sends a platform administrator away from tenant routes', async () => {
    renderApp(authFor('platform_admin', ['platform.registrations.read']), '/upload')

    expect(await screen.findByText('无权访问此页面')).toBeInTheDocument()
  })

  it('sends a tenant administrator to the workspace after a forbidden redirect', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(TENANT_ADMIN_SESSION)
    renderLogin('/forbidden')

    await submitLogin()

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/upload')
    })
  }, 15000)

  it('sends a platform administrator home after a tenant login redirect', async () => {
    vi.spyOn(authApi, 'login').mockResolvedValue(PLATFORM_ADMIN_SESSION)
    renderLogin('/upload')

    await submitLogin()

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/platform/registrations')
    })
  }, 15000)

  it('stores the current rule set under the tenant-specific key', async () => {
    renderApp(
      authFor('tenant_admin', ['review.view', 'review.execute']),
      '/upload',
    )

    await screen.findByText('审查规则集')
    await waitFor(() => {
      expect(localStorage.getItem('current_rule_set_id:tenant-1')).toBe('rs-1')
    })
    expect(localStorage.getItem('current_rule_set_id')).toBeNull()
  })
})
