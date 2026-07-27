import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端默认在 http://localhost:8800
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        // 优先用 start.bat 注入的实际后端地址（VITE_API_TARGET），
        // 端口冲突自动避让后前端能自动跟随，无需手改本文件。
        target: process.env.VITE_API_TARGET || 'http://localhost:18800',
        changeOrigin: true,
      },
    },
  },
})
