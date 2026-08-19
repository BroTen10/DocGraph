/**
 * PDF.js worker 配置
 *
 * react-pdf 内部使用 PDF.js，需要配置 worker 文件路径。
 * 在 Vite 中使用 import.meta.url 解析 worker 路径。
 */

import { pdfjs } from 'react-pdf'

/**
 * react-pdf v10 的文本层样式必须显式引入。
 * 不引入时文本层是普通块级元素、没有绝对定位，会渲染在画布下方，
 * 导致高亮 mark 与原始文档不在同一图层/位置，看不到高亮在哪。
 */
import 'react-pdf/dist/Page/TextLayer.css'

// 配置 workerSrc：Vite 会自动解析这个 URL
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString()
