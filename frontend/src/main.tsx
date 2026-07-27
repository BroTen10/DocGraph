import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import 'dayjs/locale/zh-cn'
import dayjs from 'dayjs'
import App from './App'
import './index.css'

dayjs.locale('zh-cn')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          // 紫蓝渐变品牌色
          colorPrimary: '#6366f1',
          colorLink: '#6366f1',
          colorLinkHover: '#8b5cf6',
          colorInfo: '#6366f1',
          // 圆角
          borderRadius: 8,
          borderRadiusLG: 12,
          borderRadiusSM: 6,
          // 字体
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', Helvetica, Arial, sans-serif",
          fontSize: 14,
          // 颜色
          colorBgLayout: '#f8fafc',
          colorBgContainer: '#ffffff',
          colorBorder: '#e2e8f0',
          colorTextSecondary: '#64748b',
          // 阴影
          boxShadowSecondary:
            '0 1px 3px rgba(15, 23, 42, 0.04), 0 1px 2px rgba(15, 23, 42, 0.06)',
        },
        components: {
          Layout: {
            headerBg: '#ffffff',
            headerHeight: 60,
            headerPadding: '0 24px',
            bodyBg: '#f8fafc',
            siderBg: '#ffffff',
          },
          Menu: {
            itemSelectedBg: 'rgba(99, 102, 241, 0.08)',
            itemSelectedColor: '#6366f1',
            itemHoverBg: 'rgba(99, 102, 241, 0.04)',
            itemBorderRadius: 8,
            itemMarginInline: 8,
          },
          Card: {
            borderRadiusLG: 12,
            headerBg: 'transparent',
            headerFontSize: 15,
            paddingLG: 20,
          },
          Table: {
            headerBg: '#f8fafc',
            headerColor: '#475569',
            headerSplitColor: 'transparent',
            rowHoverBg: '#f8fafc',
            borderColor: '#e2e8f0',
            cellPaddingBlock: 12,
          },
          Button: {
            controlHeight: 32,
            fontWeight: 500,
            primaryShadow: '0 2px 6px rgba(99, 102, 241, 0.25)',
          },
          Tag: {
            borderRadiusSM: 6,
          },
          Statistic: {
            contentFontSize: 22,
          },
          Progress: {
            defaultColor: '#6366f1',
          },
          Tabs: {
            inkBarColor: '#6366f1',
            itemActiveColor: '#6366f1',
            itemSelectedColor: '#6366f1',
            itemHoverColor: '#8b5cf6',
            titleFontSize: 15,
          },
        },
        algorithm: antdTheme.defaultAlgorithm,
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
)
