import { Tabs } from 'antd'
import { SettingOutlined } from '@ant-design/icons'
import PageHeader from '../components/PageHeader'
import MembersTab from './settings/MembersTab'
import RuntimeSettingsTab from './settings/RuntimeSettingsTab'

const tabs = [
  {
    key: 'runtime',
    label: '运行设置',
    children: <RuntimeSettingsTab />,
  },
  {
    key: 'members',
    label: '用户与权限',
    children: <MembersTab />,
  },
]

export default function SettingsPage() {
  return (
    <div>
      <PageHeader
        title="系统设置"
        subtitle="管理当前租户的运行参数和成员权限。仅租户管理员可以访问此页面。"
        icon={<SettingOutlined />}
        bottomGap={12}
      />
      <Tabs defaultActiveKey="runtime" items={tabs} />
    </div>
  )
}
