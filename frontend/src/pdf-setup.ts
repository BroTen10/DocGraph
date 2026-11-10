/**
 * PDF.js worker 配置
 *
 * react-pdf 内部使用 PDF.js，需要配置 worker 文件路径。
 * 在 Vite 中使用 ?url 导入 worker 资源。
 */

import { pdfjs } from 'react-pdf'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

/**
 * react-pdf v10 的文本层样式必须显式引入。
 * 不引入时文本层是普通块级元素、没有绝对定位，会渲染在画布下方，
 * 导致高亮 mark 与原始文档不在同一图层/位置，看不到高亮在哪。
 */
import 'react-pdf/dist/Page/TextLayer.css'

/**
 * worker 缓存键版本号。
 *
 * worker 是「打开 OCR 对照视图」时才懒加载的：一旦某个时刻 nginx 给它的响应头有误
 * （例如 .mjs 缺 MIME 映射、被当成 application/octet-stream），错误响应会被浏览器
 * 按 max-age 缓存住，此后不再发网络请求、直接失败，且 Ctrl+Shift+R 也救不回来
 * —— 强刷的「跳过缓存」只对本次导航加载期内发起的请求生效，懒加载的 worker 不受益。
 *
 * 改这个版本号即可换掉 URL，对所有客户端强制重新拉取 worker。
 */
const WORKER_CACHE_VERSION = '2'

// 使用 Vite 的 ?url 导入。直接写 new URL('pdfjs-dist/...') 在开发环境
// 会被解析成 /src/pdfjs-dist/...，导致 worker 动态导入失败。
pdfjs.GlobalWorkerOptions.workerSrc = `${pdfWorkerUrl}?v=${WORKER_CACHE_VERSION}`
