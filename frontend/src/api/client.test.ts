import { AxiosError, AxiosHeaders } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  apiHttp,
  authApi,
  clearCsrfToken,
  getApiErrorCode,
  graphApi,
  setCsrfToken,
} from './client'

const originalAdapter = apiHttp.defaults.adapter

afterEach(() => {
  apiHttp.defaults.adapter = originalAdapter
  clearCsrfToken()
  vi.restoreAllMocks()
})

describe('API authentication plumbing', () => {
  it('sends cookies and the in-memory CSRF token on unsafe requests', async () => {
    let capturedHeaders: AxiosHeaders | undefined
    apiHttp.defaults.adapter = async (config) => {
      capturedHeaders = AxiosHeaders.from(config.headers)
      return {
        data: {},
        status: 200,
        statusText: 'OK',
        headers: new AxiosHeaders(),
        config,
      }
    }
    setCsrfToken('csrf-test-token')

    await apiHttp.post('/settings', { items: [] })

    expect(apiHttp.defaults.withCredentials).toBe(true)
    expect(capturedHeaders?.get('X-CSRF-Token')).toBe('csrf-test-token')
  })

  it('emits session-expired only for an unauthorized response', async () => {
    const listener = vi.fn()
    window.addEventListener('docgraph:session-expired', listener)
    setCsrfToken('stale-token')
    apiHttp.defaults.adapter = async (config) => {
      const response = {
        data: { detail: 'expired' },
        status: 401,
        statusText: 'Unauthorized',
        headers: new AxiosHeaders(),
        config,
      }
      throw new AxiosError(
        'Unauthorized',
        'ERR_BAD_REQUEST',
        config,
        undefined,
        response,
      )
    }

    await expect(apiHttp.get('/protected')).rejects.toBeInstanceOf(AxiosError)
    expect(listener).toHaveBeenCalledTimes(1)

    let headersAfterExpiry: AxiosHeaders | undefined
    apiHttp.defaults.adapter = async (config) => {
      headersAfterExpiry = AxiosHeaders.from(config.headers)
      return {
        data: {},
        status: 200,
        statusText: 'OK',
        headers: new AxiosHeaders(),
        config,
      }
    }
    await apiHttp.post('/after-expiry', {})
    expect(headersAfterExpiry?.get('X-CSRF-Token')).toBeUndefined()
    window.removeEventListener('docgraph:session-expired', listener)
  })

  it('does not emit session-expired for a failed public login', async () => {
    const listener = vi.fn()
    window.addEventListener('docgraph:session-expired', listener)
    apiHttp.defaults.adapter = async (config) => {
      const response = {
        data: { detail: '邮箱或密码错误' },
        status: 401,
        statusText: 'Unauthorized',
        headers: new AxiosHeaders(),
        config,
      }
      throw new AxiosError(
        'Unauthorized',
        'ERR_BAD_REQUEST',
        config,
        undefined,
        response,
      )
    }

    await expect(authApi.login('user@example.com', 'WrongPassword1'))
      .rejects.toBeInstanceOf(AxiosError)
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener('docgraph:session-expired', listener)
  })

  it('preserves backend business error codes', () => {
    expect(getApiErrorCode({
      response: { data: { code: 'LAST_TENANT_ADMIN' } },
    })).toBe('LAST_TENANT_ADMIN')
    expect(getApiErrorCode({
      response: { data: { detail: { code: 'MEMBER_DISABLED' } } },
    })).toBe('MEMBER_DISABLED')
  })

  it.each(['put', 'patch', 'delete'] as const)(
    'injects CSRF on %s requests',
    async (method) => {
      let capturedHeaders: AxiosHeaders | undefined
      apiHttp.defaults.adapter = async (config) => {
        capturedHeaders = AxiosHeaders.from(config.headers)
        return {
          data: {},
          status: 200,
          statusText: 'OK',
          headers: new AxiosHeaders(),
          config,
        }
      }
      setCsrfToken('csrf-method-token')

      await apiHttp.request({ method, url: '/protected', data: {} })

      expect(capturedHeaders?.get('X-CSRF-Token')).toBe('csrf-method-token')
    },
  )

  it('does not send a client-controlled graph operator', async () => {
    let capturedParams: Record<string, unknown> | undefined
    apiHttp.defaults.adapter = async (config) => {
      capturedParams = config.params as Record<string, unknown>
      return {
        data: { task_id: 'task-1', message: 'started' },
        status: 200,
        statusText: 'OK',
        headers: new AxiosHeaders(),
        config,
      }
    }

    await graphApi.buildAsync('ruleset-1', true)

    expect(capturedParams).toEqual({
      auto_confirm_all: true,
      rule_set_id: 'ruleset-1',
    })
  })
})
