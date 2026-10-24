import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

const nativeGetComputedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = (element: Element) => nativeGetComputedStyle(element)

Object.defineProperty(globalThis, '__BUILD_INFO__', {
  configurable: true,
  value: {
    kind: 'frontend',
    name: 'DocGraph',
    version: '0.0.0-test',
    label: '0.0.0-test',
    describe: null,
    tag: null,
    commit: null,
    branch: null,
    dirty: false,
    build_env: 'test',
    built_at: '2026-10-24T00:00:00Z',
    source: 'build-args',
  },
})

if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  })
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

afterEach(() => {
  cleanup()
})
