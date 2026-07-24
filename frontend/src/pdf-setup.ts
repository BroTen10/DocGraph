/**
 * PDF.js worker 配置
 *
 * react-pdf 内部使用 PDF.js，需要配置 worker 文件路径。
 * 在 Vite 中使用 import.meta.url 解析 worker 路径。
 */

import { pdfjs } from 'react-pdf'

// 配置 workerSrc：Vite 会自动解析这个 URL
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString()
