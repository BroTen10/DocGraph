import { useState, useEffect } from 'react'
import { load as yamlLoad } from 'js-yaml'
import {
  Table, Tag, Button, Modal, Form, Input, Space, message, Typography, Tooltip,
  Popconfirm, Switch, Alert, Radio,
} from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined, WarningOutlined, BulbOutlined } from '@ant-design/icons'
import { skillsApi, getErrorDetail, getErrorMessage } from '../api/client'
import type { RuleParseSkill, RuleParseSkillCreate, RuleParseSkillUpdate } from '../types'

const { Text } = Typography
const { TextArea } = Input

interface Props {
  ruleSetId: string
}

/** 新建 Skill 的 YAML 模板（YAML 支持注释，直接在模板里教用户写法） */
const YAML_TEMPLATE = `# ===== 通用贸易文档审查规则解析 Skill =====
# 覆盖场景：出口（代理/自营/退税/不退税）、进口、转口、内贸
# 四维检查项：齐套性 / 基础判断 / 信息准确性 / 时间逻辑
# ============================================
# 只需保留你要用的段落，其余可整段删除。

# 1) 解析指令：逐条注入大模型提示词
prompt_instructions:
  # 文件类型（根据实际业务增删）
  - 文件类型从以下枚举选择：报关单、委托出口确认单、代理协议、采购合同、销售合同、商业发票、装箱单、提单（海运提单/空运提单）、原产地证、保险单、报关委托书、产品清单、出入仓单、派车单、运输单据（运输合同/运单）、水单（收汇/付汇水单）、形式发票、信用证、质检单、重量单、报关单草稿
  - 检查项从以下枚举选择：齐套性、基础判断、信息准确性、时间逻辑
  # 齐套性：文件是否齐全
  - 齐套性规则聚焦：关键单据是否齐全、是否缺失、是否有替代文件
  - 条件齐套用 condition 标记：仅当某场景或系统选项开启时才必备
  # 基础判断：用印、签章、关联
  - 基础判断聚焦：用印一致性（印章匹配）、签章完整性、企业与单据关联关系
  - 一致类规则（应一致/应匹配）拆为 operator== 断言
  # 信息准确性：金额/数量/产品信息
  - 信息准确性聚焦：金额比对、数量比对、产品信息（名称/型号/HS编码）一致性
  - 金额比对用 operator: ≤ / == / ≠，显式写出比较方向
  - 数量比对同样处理：出口数量不可大于委托数量 对应 operator: ≤
  # 时间逻辑：日期先后
  - 时间逻辑聚焦：日期先后关系、有效期覆盖、合同期限
  - 时间链规则需按时间先后顺序输出，标注 pairwise
  # 通用规则
  - 每条规则只含一个断言。若原文含多条规则，分别拆为独立条目输出
  - 比较方向必须显式输出 operator 字段（可选值：==/≤/≥/≠/覆盖/加总≤）
  - 规则若明确写 待人工确认 交人工判定 等词，severity 设为 warning
  - 若规则含例外条款（如 税款除外 另有约定除外），保留为 exception 字段
  - 若原文缺失比较阈值或参考对象，标记缺陷类型 incomplete_condition

# 2) 字段值映射：{字段名: {原值: 目标值}}
field_mappings:
  check_category:
    齐套: 齐套性
    基础: 基础判断
    信息: 信息准确性
    时间: 时间逻辑
    必备性: 齐套性
  severity:
    人工确认: warning
    人工判定: warning
    人工核实: warning

# 3) 默认值：容差与优先级
defaults:
  tolerance:
    amount_percent: 0
    weight_kg: 0
  priority:
    齐套性: 10
    基础判断: 20
    信息准确性: 30
    时间逻辑: 40

# 4) 结果校验规则
validations:
  - field: defaults.tolerance.amount_percent
    rule: 值必须在 0-100 之间
    severity: error
    message: 金额容差超出 0-100 范围
  - field: defaults.tolerance.weight_kg
    rule: 值必须 >= 0
    severity: error

# 5) 文本预处理（正则替换，解析前执行）
text_preprocessing:
  - type: regex
    pattern: 我我司
    replacement: 我司
  - type: regex
    pattern: (\.\.+|…+)
    replacement: …
  - type: regex
    pattern: \\b\\d+\\)(?!\\s*[\\u4e00-\\u9fff])
    replacement: ""
  - type: regex
    pattern: \s{3,}
    replacement: " "

# 6) 术语归一：{标准词: [别名列表]}
# 注意：短词会被朴素替换污染，只放安全长词条
term_normalization:
  委托出口确认单:
    - 委托出口代理订单
    - 委托确认单
  报关单:
    - 出口报关单
    - 进口报关单
  代理协议:
    - 代理合同
    - 委托代理协议
  运输单据:
    - 运输合同
    - 运单
  出入仓单:
    - 入库单
    - 出库单
    - 入仓单
    - 出仓单
  商业发票:
    - 形式发票
  产品清单:
    - 产品明细
    - 规格明细

# 7) 领域上下文：词汇表与常见模式
domain_context:
  glossary:
    齐套性: 检查规定过程文件是否齐全、完备
    基础判断: 检查文件的印章、签章、企业关联等基础信息
    信息准确性: 检查金额、数量、产品信息等数据准确性
    时间逻辑: 检查日期、期限等时间关系的合理性
    N9B系统: 贸易业务管理系统，存储合同、订单、出入仓等信息
    HS编码: 海关编码，用于商品归类
    回签: 合同双方签字盖章确认
    大合同: 框架性采购/销售合同，分批执行
    虚仓: 不在实体仓库中转存储的业务场景
    备案印章: 在合同中备案的额外可用印章
  common_patterns:
    - 金额比对类规则通常涉及报关单金额 vs 委托单金额
    - 数量比对类规则通常涉及报关单数量 vs 委托单数量
    - 日期链：签订日期 < 报关日期 < 提单日期 < 装运日期 < 发/收货日期
    - 产品信息：产品名称/规格型号/HS编码在多单据间一致
    - 大合同与分批单：分批金额之和不可超过大合同总额
`

export default function SkillTab({ ruleSetId }: Props) {
  const [skills, setSkills] = useState<RuleParseSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<RuleParseSkill | null>(null)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [yamlText, setYamlText] = useState('')
  const [yamlError, setYamlError] = useState('')
  // 纠偏引导（向导式生成最小 YAML，降低用户编写门槛）
  const [wizardOpen, setWizardOpen] = useState(false)
  const [wizType, setWizType] = useState<'term' | 'mapping' | 'instruction'>('term')
  const [wizTermCanonical, setWizTermCanonical] = useState('')
  const [wizTermVariants, setWizTermVariants] = useState('')
  const [wizMappingField, setWizMappingField] = useState('')
  const [wizMappingFrom, setWizMappingFrom] = useState('')
  const [wizMappingTo, setWizMappingTo] = useState('')
  const [wizInstruction, setWizInstruction] = useState('')

  const openWizard = () => {
    setWizType('term')
    setWizTermCanonical('')
    setWizTermVariants('')
    setWizMappingField('')
    setWizMappingFrom('')
    setWizMappingTo('')
    setWizInstruction('')
    setWizardOpen(true)
  }

  const generateFromWizard = () => {
    let yaml = ''
    if (wizType === 'term') {
      const canonical = wizTermCanonical.trim()
      const variants = wizTermVariants.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
      if (!canonical || variants.length === 0) {
        message.warning('请填写标准词和至少一个别名')
        return
      }
      yaml = '# 术语归一：把同义说法统一为标准词\nterm_normalization:\n' +
        `  ${canonical}:\n` + variants.map((v) => `    - ${v}`).join('\n') + '\n'
    } else if (wizType === 'mapping') {
      const field = wizMappingField.trim()
      const from = wizMappingFrom.trim()
      const to = wizMappingTo.trim()
      if (!field || !from || !to) {
        message.warning('请填写字段名、原值、目标值')
        return
      }
      yaml = '# 字段映射：把解析错的取值纠正为目标值\nfield_mappings:\n' +
        `  ${field}:\n    ${from}: ${to}\n`
    } else {
      const text = wizInstruction.trim()
      if (!text) {
        message.warning('请填写解析指令')
        return
      }
      yaml = '# 解析指令：追加到解析提示词中纠偏\nprompt_instructions:\n' +
        `  - ${text}\n`
    }
    setEditing(null)
    setName('纠偏 Skill')
    setDescription('由纠偏引导生成，请核对后保存')
    setYamlText(yaml)
    setYamlError('')
    setWizardOpen(false)
    setModalOpen(true)
  }

  const load = async () => {
    if (!ruleSetId) return
    setLoading(true)
    try {
      const data = await skillsApi.list(ruleSetId)
      setSkills(data)
    } catch (e) {
      message.error('加载 Skill 失败: ' + getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [ruleSetId])

  const openCreate = () => {
    setEditing(null)
    setName('')
    setDescription('')
    setYamlText(YAML_TEMPLATE)
    setYamlError('')
    setModalOpen(true)
  }

  const openEdit = (skill: RuleParseSkill) => {
    setEditing(skill)
    setName(skill.name)
    setDescription(skill.description || '')
    setYamlText(skill.content_yaml || '')
    setYamlError('')
    setModalOpen(true)
  }

  const handleSave = async () => {
    const trimmedName = name.trim()
    if (!trimmedName) {
      message.warning('请填写 Skill 名称')
      return
    }
    // 批次 5-4：前端 YAML 预校验，语法错误直接拦截，避免后端往返
    if (!yamlText.trim()) {
      setYamlError('Skill 内容不能为空')
      return
    }
    try {
      yamlLoad(yamlText)
    } catch (yamlErr) {
      const msg = yamlErr instanceof Error ? yamlErr.message.replace(/^YAMLException:\s*/, '') : String(yamlErr)
      setYamlError(`YAML 语法错误：${msg}`)
      message.error('YAML 语法错误，请修正后保存')
      return
    }
    setYamlError('')
    setSaving(true)
    try {
      if (editing) {
        const payload: RuleParseSkillUpdate = { content_yaml: yamlText }
        // 编辑内置产生副本时也带上名称/描述；编辑自定义时仅在有变化时提交
        if (trimmedName !== editing.name) payload.name = trimmedName
        if (description !== (editing.description || '')) payload.description = description
        const saved = await skillsApi.update(ruleSetId, editing.id, payload)
        message.success(
          editing.is_builtin
            ? `已保存为自定义副本「${saved.name}」（内置默认未改动）`
            : `Skill 已更新（v${saved.version}）`
        )
      } else {
        const payload: RuleParseSkillCreate = {
          name: trimmedName,
          description,
          content_yaml: yamlText,
        }
        const saved = await skillsApi.create(ruleSetId, payload)
        message.success(`Skill「${saved.name}」已创建`)
      }
      setModalOpen(false)
      await load()
    } catch (e) {
      const detail = getErrorDetail(e)
      if (typeof detail === 'string' && detail.includes('YAML')) {
        setYamlError(detail)
      } else {
        message.error('保存失败: ' + getErrorMessage(e))
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (skill: RuleParseSkill) => {
    try {
      await skillsApi.delete(ruleSetId, skill.id)
      message.success('已删除')
      await load()
    } catch (e) {
      message.error('删除失败: ' + getErrorMessage(e))
    }
  }

  const handleToggleEnabled = async (skill: RuleParseSkill, enabled: boolean) => {
    try {
      await skillsApi.update(ruleSetId, skill.id, { enabled })
      message.success(`「${skill.name}」已${enabled ? '启用' : '停用'}`)
      await load()
    } catch (e) {
      message.error('更新失败: ' + getErrorMessage(e))
    }
  }

  const columns = [
    {
      title: '名称', dataIndex: 'name', key: 'name', width: 200,
      render: (v: string, row: RuleParseSkill) => (
        <Space size={4}>
          <Text strong={!row.is_builtin}>{v}</Text>
          {row.parent_id && <Tooltip title="由编辑内置默认产生的副本"><Tag color="purple" style={{ fontSize: 11 }}>副本</Tag></Tooltip>}
        </Space>
      ),
    },
    {
      title: '来源', key: 'type', width: 80,
      render: (_: unknown, row: RuleParseSkill) => (
        row.is_builtin ? <Tag color="blue">内置</Tag> : <Tag>自定义</Tag>
      ),
    },
    {
      title: '启用', key: 'enabled', width: 70,
      render: (_: unknown, row: RuleParseSkill) => (
        <Tooltip title={row.is_builtin ? '内置 Skill 为全局，停用将影响所有规则集' : undefined}>
          <Switch size="small" checked={row.enabled} onChange={(v) => handleToggleEnabled(row, v)} />
        </Tooltip>
      ),
    },
    {
      title: '版本', dataIndex: 'version', key: 'version', width: 60,
      render: (v: number) => <Tag style={{ fontSize: 11 }}>v{v}</Tag>,
    },
    {
      title: '描述', dataIndex: 'description', key: 'description', ellipsis: true,
      render: (v: string | null) => v || <Text type="secondary">-</Text>,
    },
    {
      title: '能力概况', key: 'summary', width: 220,
      render: (_: unknown, row: RuleParseSkill) => {
        const c = row.content || ({} as RuleParseSkill['content'])
        const tags: string[] = []
        if ((c.prompt_instructions || []).length) tags.push(`指令×${c.prompt_instructions.length}`)
        if (Object.keys(c.field_mappings || {}).length) tags.push('映射')
        if (Object.keys(c.defaults || {}).length) tags.push('默认值')
        if ((c.validations || []).length) tags.push(`校验×${c.validations.length}`)
        if ((c.text_preprocessing || []).length) tags.push(`预处理×${c.text_preprocessing.length}`)
        if (Object.keys(c.term_normalization || {}).length) tags.push('术语归一')
        if (Object.keys(c.domain_context?.glossary || {}).length || (c.domain_context?.common_patterns || []).length) tags.push('领域')
        return tags.length ? <Space size={4} wrap>{tags.map((t) => <Tag key={t} style={{ fontSize: 11 }}>{t}</Tag>)}</Space> : <Text type="secondary">空</Text>
      },
    },
    {
      title: '操作', key: 'action', width: 110,
      render: (_: unknown, row: RuleParseSkill) => (
        <Space>
          <Tooltip title={row.is_builtin ? '编辑（保存为自定义副本）' : '编辑'}>
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} />
          </Tooltip>
          {!row.is_builtin && (
            <Popconfirm title="确定删除此 Skill？" onConfirm={() => handleDelete(row)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建 Skill</Button>
          <Button icon={<BulbOutlined />} onClick={openWizard}>纠偏引导</Button>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </Space>
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 8 }}
          message={
            <span>
              Skill 用 <Text code>YAML</Text> 描述，控制大模型解析规则的过程；导入规则时自动应用已启用的 Skill。
              启用/停用不产生新版本；仅内容修改会使版本 +1。
              人工修正解析错误的规则时，可将修正经验自动写入「经验修正（自动累积）」Skill。
            </span>
          }
        />
      </div>

      <Table
        dataSource={skills}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={false}
        loading={loading}
      />

      <Modal
        title="纠偏引导：按意图生成最小 Skill"
        open={wizardOpen}
        onOk={generateFromWizard}
        onCancel={() => setWizardOpen(false)}
        okText="生成到编辑器"
        cancelText="取消"
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="Skill 只做纠偏，不需要从零写解析规则。选择一种纠偏意图，系统生成对应 YAML，你核对后保存即可。"
        />
        <Form layout="vertical">
          <Form.Item label="纠偏意图" required>
            <Radio.Group value={wizType} onChange={(e) => setWizType(e.target.value)}>
              <Radio.Button value="term">术语归一</Radio.Button>
              <Radio.Button value="mapping">字段取值纠正</Radio.Button>
              <Radio.Button value="instruction">补充解析指令</Radio.Button>
            </Radio.Group>
          </Form.Item>
          {wizType === 'term' && (
            <>
              <Form.Item label="标准词" required>
                <Input
                  value={wizTermCanonical}
                  onChange={(e) => setWizTermCanonical(e.target.value)}
                  placeholder="如：委托出口确认单"
                />
              </Form.Item>
              <Form.Item label="别名（逗号分隔，统一为标准词）" required>
                <Input
                  value={wizTermVariants}
                  onChange={(e) => setWizTermVariants(e.target.value)}
                  placeholder="如：委托出口代理订单,委托确认单"
                />
              </Form.Item>
            </>
          )}
          {wizType === 'mapping' && (
            <>
              <Form.Item label="字段名" required>
                <Input
                  value={wizMappingField}
                  onChange={(e) => setWizMappingField(e.target.value)}
                  placeholder="如：check_category"
                />
              </Form.Item>
              <Form.Item label="原值（解析错了的值）" required>
                <Input
                  value={wizMappingFrom}
                  onChange={(e) => setWizMappingFrom(e.target.value)}
                  placeholder="如：齐套"
                />
              </Form.Item>
              <Form.Item label="目标值（应纠正为）" required>
                <Input
                  value={wizMappingTo}
                  onChange={(e) => setWizMappingTo(e.target.value)}
                  placeholder="如：齐套性"
                />
              </Form.Item>
            </>
          )}
          {wizType === 'instruction' && (
            <Form.Item label="解析指令" required>
              <Input.TextArea
                rows={4}
                value={wizInstruction}
                onChange={(e) => setWizInstruction(e.target.value)}
                placeholder="如：本规则集中『验收单』一律指『验收确认单』；金额比对必须显式给出比较方向"
              />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={editing ? `编辑 Skill：${editing.name}${editing.is_builtin ? '（保存为自定义副本）' : ''}` : '新建 Skill'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={860}
        confirmLoading={saving}
        okText="保存"
      >
        {editing?.is_builtin && (
          <Alert
            type="warning"
            showIcon
            icon={<WarningOutlined />}
            style={{ marginBottom: 12 }}
            message="编辑内置默认 Skill 将在当前规则集下创建自定义副本，原始内置不受影响。如只想停用内置，请直接用列表中的开关。"
          />
        )}
        <Form layout="vertical">
          <Space style={{ width: '100%' }} align="start" size={16}>
            <Form.Item label="名称" required style={{ width: 320 }}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：贸易合同校验解析 Skill"
                maxLength={100}
              />
            </Form.Item>
            <Form.Item label="描述" style={{ width: 460 }}>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Skill 用途说明（可选）"
              />
            </Form.Item>
          </Space>
          <Form.Item
            label={
              <Space>
                <span>Skill 内容（YAML，支持 # 注释）</span>
              </Space>
            }
            validateStatus={yamlError ? 'error' : undefined}
            help={yamlError || undefined}
          >
            <TextArea
              rows={22}
              value={yamlText}
              onChange={(e) => { setYamlText(e.target.value); setYamlError('') }}
              placeholder="输入 YAML 格式的 Skill 内容..."
              style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 12 }}
              spellCheck={false}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
