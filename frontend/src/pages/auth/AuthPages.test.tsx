import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { authApi } from '../../api/client'
import {
  AuthProvider,
  type AuthContextValue,
} from '../../auth/AuthContext'
import AcceptInvitePage from './AcceptInvitePage'
import LoginPage from './LoginPage'
import PendingPage from './PendingPage'
import RegisterPage from './RegisterPage'
import ResetPasswordPage from './ResetPasswordPage'

const AUTH: AuthContextValue = {
  loading: false,
  initializationError: null,
  user: {
    id: 'u1',
    name: '张三',
    email: 'zhang@example.com',
    phone_masked: '138****8000',
    role: 'tenant_admin',
  },
  tenant: { id: 't1', name: '甲公司' },
  role: 'tenant_admin',
  permissions: new Set(['profile.read']),
  login: vi.fn(),
  logout: vi.fn(),
  refresh: vi.fn(),
  hasPermission: () => true,
}

function renderPage(route: string, element: React.ReactNode, auth = AUTH) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthProvider initialAuth={auth}>
        <Routes>
          <Route path={route.split('?')[0]} element={element} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('authentication page errors', () => {
  it('shows a login rejection without treating it as session expiry', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new Error('邮箱或密码错误'))
    renderPage('/login', <LoginPage />, AUTH)

    await userEvent.type(screen.getByPlaceholderText('name@example.com'), 'zhang@example.com')
    await userEvent.type(screen.getByPlaceholderText('请输入密码'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: /登\s*录/ }))

    expect(await screen.findByText('邮箱或密码错误')).toBeInTheDocument()
  })

  it('shows registration validation feedback', async () => {
    renderPage('/register', <RegisterPage />, AUTH)

    await userEvent.type(screen.getByPlaceholderText('name@company.com'), 'not-an-email')
    await userEvent.click(screen.getByRole('button', { name: /提交.*开通申请/ }))

    expect(await screen.findByText('邮箱格式不正确')).toBeInTheDocument()
  })

  it('shows a backend registration error on the form', async () => {
    vi.spyOn(authApi, 'register').mockRejectedValue(new Error('该邮箱已提交过申请'))
    renderPage('/register', <RegisterPage />, AUTH)

    fireEvent.change(screen.getByPlaceholderText('例如：华东贸易有限公司'), {
      target: { value: '测试贸易公司' },
    })
    fireEvent.change(screen.getByPlaceholderText('请输入真实姓名'), {
      target: { value: '张三' },
    })
    fireEvent.change(screen.getByPlaceholderText('name@company.com'), {
      target: { value: 'zhang@example.com' },
    })
    fireEvent.change(screen.getByPlaceholderText('13800138000'), {
      target: { value: '13800138000' },
    })
    fireEvent.change(screen.getByLabelText('登录密码'), {
      target: { value: 'DocGraph2026' },
    })
    fireEvent.change(screen.getByLabelText('确认密码'), {
      target: { value: 'DocGraph2026' },
    })
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: /提交.*开通申请/ }))

    expect(
      await screen.findByText('该邮箱已提交过申请', undefined, { timeout: 10000 }),
    ).toBeInTheDocument()
  }, 15000)

  it('reports missing reset and invitation tokens', () => {
    const reset = renderPage('/reset-password', <ResetPasswordPage />, AUTH)
    expect(screen.getByText('重置链接无效')).toBeInTheDocument()
    reset.unmount()

    renderPage('/accept-invite', <AcceptInvitePage />, AUTH)
    expect(screen.getByText('邀请链接无效')).toBeInTheDocument()
  })

  it('removes a consumed verification token from the address bar', async () => {
    vi.spyOn(authApi, 'verifyEmail').mockResolvedValue({
      message: '邮箱验证成功',
      status: 'pending_review',
    })
    function LocationProbe() {
      return <div data-testid="location-search">{useLocation().search}</div>
    }
    render(
      <MemoryRouter initialEntries={['/pending?token=one-time-token']}>
        <AuthProvider initialAuth={AUTH}>
          <Routes>
            <Route path="/pending" element={<PendingPage />} />
          </Routes>
          <LocationProbe />
        </AuthProvider>
      </MemoryRouter>,
    )

    expect((await screen.findAllByText('邮箱验证成功')).length).toBeGreaterThan(0)
    await waitFor(() => expect(screen.getByTestId('location-search')).toHaveTextContent(''))
  })
})
