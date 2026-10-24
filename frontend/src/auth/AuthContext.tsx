import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { authApi } from '../api/client'
import type {
  AuthSessionResponse,
  AuthUser,
  Permission,
  TenantInfo,
  UserRole,
} from '../types'

export interface AuthContextValue {
  loading: boolean
  user: AuthUser | null
  tenant: TenantInfo | null
  role: UserRole | null
  permissions: Set<string>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
  hasPermission: (permission: Permission) => boolean
}

interface AuthProviderProps {
  children: ReactNode
  /**
   * Test and embedding seam. When explicitly null, the provider starts as an
   * anonymous session without requesting /auth/me.
   */
  initialAuth?: AuthContextValue | null
}

const AuthContext = createContext<AuthContextValue | null>(null)

const PUBLIC_AUTH_PATHS = new Set([
  '/login',
  '/register',
  '/pending',
  '/verify-email',
  '/forgot-password',
  '/reset-password',
  '/accept-invite',
])

function stateFromSession(session: AuthSessionResponse): Omit<AuthContextValue, 'login' | 'logout' | 'refresh' | 'hasPermission'> {
  return {
    loading: false,
    user: session.user,
    tenant: session.tenant,
    role: session.role,
    permissions: new Set(session.permissions),
  }
}

const ANONYMOUS_AUTH = {
  loading: false,
  user: null,
  tenant: null,
  role: null,
  permissions: new Set<string>(),
} satisfies Omit<AuthContextValue, 'login' | 'logout' | 'refresh' | 'hasPermission'>

export function AuthProvider({ children, initialAuth }: AuthProviderProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const hasExplicitInitialAuth = initialAuth !== undefined
  const [loading, setLoading] = useState(
    initialAuth === null ? false : initialAuth?.loading ?? !hasExplicitInitialAuth,
  )
  const [user, setUser] = useState<AuthUser | null>(initialAuth?.user ?? null)
  const [tenant, setTenant] = useState<TenantInfo | null>(initialAuth?.tenant ?? null)
  const [role, setRole] = useState<UserRole | null>(initialAuth?.role ?? null)
  const [permissions, setPermissions] = useState<Set<string>>(
    () => new Set(initialAuth?.permissions ?? []),
  )
  const locationRef = useRef(location.pathname)

  useEffect(() => {
    locationRef.current = location.pathname
  }, [location.pathname])

  const applySession = useCallback((session: AuthSessionResponse) => {
    const next = stateFromSession(session)
    setUser(next.user)
    setTenant(next.tenant)
    setRole(next.role)
    setPermissions(next.permissions)
    setLoading(false)
  }, [])

  const clearSession = useCallback(() => {
    setUser(ANONYMOUS_AUTH.user)
    setTenant(ANONYMOUS_AUTH.tenant)
    setRole(ANONYMOUS_AUTH.role)
    setPermissions(new Set())
    setLoading(false)
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      applySession(await authApi.me())
    } catch {
      clearSession()
    }
  }, [applySession, clearSession])

  const login = useCallback(async (email: string, password: string) => {
    setLoading(true)
    try {
      applySession(await authApi.login(email, password))
    } catch (error) {
      clearSession()
      throw error
    }
  }, [applySession, clearSession])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      clearSession()
    }
  }, [clearSession])

  useEffect(() => {
    if (hasExplicitInitialAuth) return
    void refresh()
  }, [hasExplicitInitialAuth, refresh])

  useEffect(() => {
    const onSessionExpired = () => {
      clearSession()
      if (!PUBLIC_AUTH_PATHS.has(locationRef.current)) {
        navigate('/login', { replace: true, state: { from: locationRef.current } })
      }
    }
    window.addEventListener('docgraph:session-expired', onSessionExpired)
    return () => window.removeEventListener('docgraph:session-expired', onSessionExpired)
  }, [clearSession, navigate])

  const hasPermission = useCallback(
    (permission: Permission) => permissions.has(permission),
    [permissions],
  )

  const value = useMemo<AuthContextValue>(
    () => ({
      loading,
      user,
      tenant,
      role,
      permissions,
      login,
      logout,
      refresh,
      hasPermission,
    }),
    [loading, user, tenant, role, permissions, login, logout, refresh, hasPermission],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) {
    throw new Error('useAuth 必须在 AuthProvider 内使用')
  }
  return value
}
