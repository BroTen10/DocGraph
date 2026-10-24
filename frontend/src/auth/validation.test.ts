import { describe, expect, it } from 'vitest'

import { isValidPassword, normalizePhone } from './validation'

describe('auth validation', () => {
  it.each([
    ['13800138000', '+8613800138000'],
    ['138 0013 8000', '+8613800138000'],
    ['138-0013-8000', '+8613800138000'],
    ['+86 13800138000', '+8613800138000'],
  ])('normalizes mainland mobile number %s', (raw, expected) => {
    expect(normalizePhone(raw)).toBe(expected)
  })

  it.each(['', '010-12345678', '+85212345678', '12800138000'])(
    'rejects invalid mainland mobile number %s',
    (raw) => {
      expect(() => normalizePhone(raw)).toThrow('中国大陆手机号')
    },
  )

  it('enforces the same password shape as the backend', () => {
    expect(isValidPassword('DocGraph2026')).toBe(true)
    expect(isValidPassword('short1')).toBe(false)
    expect(isValidPassword('1234567890')).toBe(false)
    expect(isValidPassword('abcdefghij')).toBe(false)
  })
})
