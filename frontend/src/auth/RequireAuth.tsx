import type { ReactNode } from 'react'
import { Spin } from 'antd'
import {
  Navigate,
  Outlet,
  useLocation,
} from 'react-router-dom'
import { useAuth } from './AuthContext'
import type { Permission } from '../types'

interface RequireAuthProps {
  mode: 'tenant' | 'platform'
}

export function RequireAuth({ mode }: RequireAuthProps) {
  const { loading, user, tenant, role } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="auth-loading">
        <Spin size="large" tip="正在验证登录状态..." />
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  if (mode === 'platform' && role !== 'platform_admin') {
    return <Navigate to="/forbidden" replace />
  }
  if (mode === 'tenant' && (role === 'platform_admin' || !tenant)) {
    return <Navigate to="/forbidden" replace />
  }
  return <Outlet />
}

interface RequirePermissionProps {
  permission: Permission
  children: ReactNode
}

export function RequirePermission({ permission, children }: RequirePermissionProps) {
  const { hasPermission } = useAuth()
  if (!hasPermission(permission)) {
    return <Navigate to="/forbidden" replace />
  }
  return children
}
