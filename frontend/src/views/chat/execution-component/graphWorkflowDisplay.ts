export type GraphStepStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'waiting_input'
  | 'skipped'
  | 'cancelled'

export interface GraphTraceNodeLike {
  name: string
  status: string
  route_reason?: string
  output?: any
}

export interface GraphTraceLike {
  run_id: string
  status: string
  current_node?: string
  nodes: GraphTraceNodeLike[]
}

export interface GraphPendingInteractionLike {
  interaction_id: string
  run_id: string
  node_name: string
  status: string
  prompt?: string
  options?: Array<Record<string, any>>
  response_schema?: Record<string, any>
}

export interface GraphWorkflowStep {
  key: string
  label: string
  status: GraphStepStatus
  summary: string
}

export interface NormalizedGraphInteractionOption {
  label: string
  value: any
  slot: string
  raw: Record<string, any>
}

export interface NormalizedGraphInteraction {
  title: string
  prompt: string
  targetSlots: string[]
  options: NormalizedGraphInteractionOption[]
}

export const GRAPH_NODE_LABELS: Record<string, string> = {
  classify_question: '理解问题',
  rewrite_question: '整理问题',
  ask_rewrite_clarification: '补充问题信息',
  recognize_intent: '识别分析意图',
  retrieve_knowledge: '匹配数据资产',
  ask_intent_clarification: '确认分析方式',
  ask_metric_selection: '选择分析指标',
  draw_image_profile: '选择图表形式',
  generate_sql: '生成查询',
  execute_sql: '查询数据',
  handle_sql_error: '修复查询',
  generate_question_answer: '生成答案',
  recommend_questions: '推荐追问',
  compose_final_reply: '整理回复',
  finish: '结束',
}

const INTERACTION_TITLES: Record<string, string> = {
  ask_rewrite_clarification: '请补充问题信息',
  ask_intent_clarification: '请确认分析方式',
  ask_metric_selection: '请选择要分析的指标',
}

export function buildGraphWorkflowSteps(
  trace?: GraphTraceLike | null,
  pendingInteraction?: GraphPendingInteractionLike | null
): GraphWorkflowStep[] {
  const nodes = trace?.nodes || []
  return nodes
    .filter((node) => node.status !== 'not_run')
    .map((node) => ({
      key: node.name,
      label: graphNodeLabel(node.name),
      status: graphStepStatus(node, trace, pendingInteraction),
      summary: graphNodeSummary(node, pendingInteraction),
    }))
}

export function graphProgressHeadline(
  trace?: GraphTraceLike | null,
  pendingInteraction?: GraphPendingInteractionLike | null
) {
  if (pendingInteraction?.status === 'pending') {
    return `需要你补充信息：${pendingInteraction.prompt || interactionTitle(pendingInteraction)}`
  }
  if (!trace) return '正在准备执行...'
  const failedNode = trace.nodes.find((node) => node.status === 'failed')
  if (failedNode) return `执行失败：${graphNodeLabel(failedNode.name)}`
  if (trace.status === 'cancelled') return '已停止'
  if (trace.status === 'succeeded') return '已完成'
  const runningNode =
    trace.nodes.find((node) => node.status === 'started') ||
    trace.nodes.find((node) => node.name === trace.current_node)
  if (runningNode) return `正在执行：${graphNodeLabel(runningNode.name)}...`
  return '正在执行...'
}

export function normalizeGraphInteraction(
  interaction?: GraphPendingInteractionLike | null
): NormalizedGraphInteraction {
  if (!interaction) {
    return {
      title: '',
      prompt: '',
      targetSlots: [],
      options: [],
    }
  }
  const targetSlots = interactionSlots(interaction)
  return {
    title: interactionTitle(interaction),
    prompt: interaction.prompt || interactionTitle(interaction),
    targetSlots,
    options: (interaction.options || []).map((option) => normalizeOption(option, targetSlots)),
  }
}

export function buildGraphInteractionResponse(
  interaction: GraphPendingInteractionLike,
  option?: NormalizedGraphInteractionOption,
  customValue = '',
  skipped = false
): Record<string, any> {
  if (skipped) return { skipped: true }
  const fallbackSlot = interactionSlots(interaction)[0] || 'value'
  const trimmedCustom = customValue.trim()
  if (trimmedCustom) return { [fallbackSlot]: trimmedCustom }
  if (!option) return {}
  if (option.value && typeof option.value === 'object') return option.value
  return { [option.slot || fallbackSlot]: String(option.value ?? option.label) }
}

export function graphNodeLabel(name: string) {
  return GRAPH_NODE_LABELS[name] || name
}

function graphStepStatus(
  node: GraphTraceNodeLike,
  trace?: GraphTraceLike | null,
  pendingInteraction?: GraphPendingInteractionLike | null
): GraphStepStatus {
  if (pendingInteraction?.node_name === node.name && pendingInteraction.status === 'pending') {
    return 'waiting_input'
  }
  if (node.status === 'started') return 'running'
  if (node.status === 'waiting_input') return 'waiting_input'
  if (node.status === 'succeeded') return 'succeeded'
  if (node.status === 'failed') return 'failed'
  if (node.status === 'cancelled') return 'cancelled'
  if (trace?.current_node === node.name && trace.status !== 'succeeded') return 'running'
  return 'pending'
}

function graphNodeSummary(
  node: GraphTraceNodeLike,
  pendingInteraction?: GraphPendingInteractionLike | null
) {
  if (pendingInteraction?.node_name === node.name && pendingInteraction.prompt) {
    return pendingInteraction.prompt
  }
  const output = objectOutput(node.output)
  if (node.name.startsWith('ask_')) return output.prompt || '等待用户补充信息'
  if (node.name === 'classify_question') return categoryText(output.category)
  if (node.name === 'rewrite_question') return output.rewritten_question || output.question || ''
  if (node.name === 'recognize_intent') {
    return [output.intent_type, listText(output.metric_mentions), listText(output.dimension_mentions)]
      .filter(Boolean)
      .join(' · ')
  }
  if (node.name === 'retrieve_knowledge') {
    return (
      listText(output.selected_assets?.metrics) ||
      listText(output.slot_bindings?.metrics) ||
      output.decision?.reason ||
      output.status ||
      ''
    )
  }
  if (node.name === 'draw_image_profile') return output.profile || listText(output.chart_candidates) || ''
  if (node.name === 'generate_sql') return output.sql ? '已生成 SQL' : ''
  if (node.name === 'execute_sql') {
    const rowCount = output.row_count ?? output.rows_count ?? output.data?.length
    return rowCount !== undefined ? `返回 ${rowCount} 行` : ''
  }
  if (node.name === 'handle_sql_error') return output.error || output.message || ''
  if (node.name === 'generate_question_answer') return output.answer || output.final_answer || ''
  if (node.name === 'recommend_questions') return listText(output.questions || output.recommendations)
  if (node.name === 'compose_final_reply') return output.answer || output.final_answer || output.content || ''
  return node.route_reason || ''
}

function normalizeOption(
  option: Record<string, any>,
  targetSlots: string[]
): NormalizedGraphInteractionOption {
  const value = option.value ?? option
  return {
    label: String(option.label || displayValue(value)),
    value,
    slot: inferOptionSlot(option, targetSlots),
    raw: option,
  }
}

function inferOptionSlot(option: Record<string, any>, targetSlots: string[]) {
  if (option.slot) return option.slot
  const value = option.value
  if (value && typeof value === 'object') {
    const matched = targetSlots.find((slot) => value[slot] !== undefined)
    if (matched) return matched
  }
  return targetSlots[0] || 'value'
}

function interactionSlots(interaction: GraphPendingInteractionLike) {
  const properties = interaction.response_schema?.properties
  if (!properties || typeof properties !== 'object') return ['value']
  const slots = Object.keys(properties).filter((slot) => slot !== 'skipped')
  return slots.length ? slots : ['value']
}

function interactionTitle(interaction: GraphPendingInteractionLike) {
  return INTERACTION_TITLES[interaction.node_name] || interaction.prompt || '请补充信息'
}

function objectOutput(output: any): Record<string, any> {
  return output && typeof output === 'object' ? output : {}
}

function listText(value: any) {
  if (!Array.isArray(value) || value.length === 0) return ''
  return value
    .map((item) => displayValue(item))
    .filter(Boolean)
    .join('、')
}

function displayValue(value: any): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (!value || typeof value !== 'object') return ''
  return String(
    value.display_name ||
      value.name ||
      value.label ||
      value.biz_name ||
      value.value ||
      value.asset_id ||
      ''
  )
}

function categoryText(category: any) {
  if (category === 'data') return '识别为数据查询'
  if (category === 'followup') return '识别为追问'
  if (category === 'chat') return '识别为闲聊'
  return category ? String(category) : ''
}
