import { describe, expect, it } from 'vitest'

import { hasPermission, visibleMenuKeys } from './permissions'

describe('menu permissions', () => {
  it('hides system settings from reviewers and viewers', () => {
    expect(visibleMenuKeys('reviewer')).not.toContain('/settings')
    expect(visibleMenuKeys('viewer')).not.toContain('/settings')
  })

  it('does not expose the upload workflow to read-only viewers', () => {
    expect(visibleMenuKeys('viewer')).not.toContain('/upload')
    expect(visibleMenuKeys('reviewer')).toContain('/upload')
  })

  it('shows system settings to tenant admins', () => {
    expect(visibleMenuKeys('tenant_admin')).toContain('/settings')
  })

  it('shows only platform pages to platform admins', () => {
    expect(visibleMenuKeys('platform_admin')).toEqual([
      '/platform/registrations',
      '/platform/tenants',
    ])
  })
})

describe('role permissions', () => {
  it('prevents viewers from executing reviews', () => {
    expect(hasPermission('viewer', 'review.execute')).toBe(false)
    expect(hasPermission('reviewer', 'review.execute')).toBe(true)
  })

  it('limits member and settings management to tenant admins', () => {
    expect(hasPermission('tenant_admin', 'settings.update')).toBe(true)
    expect(hasPermission('reviewer', 'settings.update')).toBe(false)
    expect(hasPermission('viewer', 'members.manage')).toBe(false)
  })

  it('does not grant tenant permissions to platform admins', () => {
    expect(hasPermission('platform_admin', 'review.view')).toBe(false)
    expect(hasPermission('platform_admin', 'platform.tenants.manage')).toBe(true)
  })
})
