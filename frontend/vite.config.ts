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
        target: 'http://localhost:8800',
        changeOrigin: true,
      },
    },
  },
})
