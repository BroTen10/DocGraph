import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export function readOneTimeToken(search: string, hash: string): string | null {
  const fragment = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash)
  return fragment.get('token') || new URLSearchParams(search).get('token')
}

/**
 * Read a one-time token from the URL fragment (with query fallback for old
 * links) and remove it from the address bar immediately after capture.
 */
export function useOneTimeToken(): string | null {
  const location = useLocation()
  const navigate = useNavigate()
  const [token] = useState(() => readOneTimeToken(location.search, location.hash))
  const cleared = useRef(false)

  useEffect(() => {
    if (!token || cleared.current) return
    cleared.current = true
    const params = new URLSearchParams(location.search)
    params.delete('token')
    const search = params.toString()
    navigate(
      {
        pathname: location.pathname,
        search: search ? `?${search}` : '',
        hash: '',
      },
      { replace: true, state: location.state },
    )
  }, [location.pathname, location.search, location.state, navigate, token])

  return token
}
