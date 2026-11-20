import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // antd 页面级用例在 jsdom 下渲染较慢，并行跑整个套件时默认 5s 容易因 CPU 争抢超时
    // （用例本身正常，只是被拖慢），这里放宽到 20s。
    testTimeout: 20000,
  },
})
