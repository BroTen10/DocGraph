import type { Permission, UserRole } from '../types'

const TENANT_MENU_KEYS = [
  '/upload',
  '/rules',
  '/doc-types',
  '/graph',
  '/review',
  '/results',
] as const

const PLATFORM_MENU_KEYS = [
  '/platform/registrations',
  '/platform/tenants',
] as const

const VIEWER_MENU_KEYS = TENANT_MENU_KEYS.filter((key) => key !== '/upload')

const REVIEWER_PERMISSIONS = new Set<Permission>([
  'profile.read',
  'profile.update',
  'review.view',
  'review.execute',
  'rules.view',
  'rules.manage',
  'doc_types.view',
  'doc_types.manage',
  'graph.view',
  'graph.manage',
])

const TENANT_ADMIN_PERMISSIONS = new Set<Permission>([
  ...REVIEWER_PERMISSIONS,
  'settings.read',
  'settings.update',
  'members.read',
  'members.manage',
  'audit.read',
])

const VIEWER_PERMISSIONS = new Set<Permission>([
  'profile.read',
  'profile.update',
  'review.view',
  'rules.view',
  'doc_types.view',
  'graph.view',
])

const PLATFORM_ADMIN_PERMISSIONS = new Set<Permission>([
  'profile.read',
  'profile.update',
  'platform.registrations.read',
  'platform.registrations.manage',
  'platform.tenants.read',
  'platform.tenants.manage',
])

const ROLE_PERMISSIONS: Record<UserRole, ReadonlySet<Permission>> = {
  tenant_admin: TENANT_ADMIN_PERMISSIONS,
  reviewer: REVIEWER_PERMISSIONS,
  viewer: VIEWER_PERMISSIONS,
  platform_admin: PLATFORM_ADMIN_PERMISSIONS,
}

export function hasPermission(role: UserRole | null | undefined, permission: Permission): boolean {
  return Boolean(role && ROLE_PERMISSIONS[role]?.has(permission))
}

export function visibleMenuKeys(role: UserRole | null | undefined): string[] {
  if (role === 'platform_admin') {
    return [...PLATFORM_MENU_KEYS]
  }
  if (role === 'tenant_admin') {
    return [...TENANT_MENU_KEYS, '/settings']
  }
  if (role === 'reviewer') {
    return [...TENANT_MENU_KEYS]
  }
  if (role === 'viewer') {
    return [...VIEWER_MENU_KEYS]
  }
  return []
}

export function homePathForRole(role: UserRole | null | undefined): string {
  return visibleMenuKeys(role)[0] ?? '/login'
}

export function postLoginPathForRole(
  role: UserRole | null | undefined,
  requestedPath: string | undefined,
): string {
  if (requestedPath && visibleMenuKeys(role).includes(requestedPath)) {
    return requestedPath
  }
  return homePathForRole(role)
}
