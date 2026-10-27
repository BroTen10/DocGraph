const CN_MOBILE_RE = /^1[3-9]\d{9}$/

export function normalizePhone(value: string): string {
  let compact = value.trim().replace(/[\s-]/g, '')
  if (compact.startsWith('+86')) {
    compact = compact.slice(3)
  } else if (compact.startsWith('86') && compact.length === 13) {
    compact = compact.slice(2)
  }
  if (!CN_MOBILE_RE.test(compact)) {
    throw new Error('请输入有效的中国大陆手机号')
  }
  return `+86${compact}`
}

export function isValidPhone(value: string): boolean {
  try {
    normalizePhone(value)
    return true
  } catch {
    return false
  }
}

export function isValidPassword(value: string): boolean {
  return (
    value.length >= 6 &&
    value.length <= 64 &&
    /[A-Za-z]/.test(value) &&
    /\d/.test(value)
  )
}

export function isValidEmail(value: string): boolean {
  const normalized = value.trim()
  return normalized.length <= 254 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(normalized)
}
