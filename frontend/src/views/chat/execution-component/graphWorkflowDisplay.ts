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
  label?: string
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
  label?: string
  status: string
  prompt?: string
  options?: Array<Record<string, any>>
  response_schema?: Record<string, any>
}

export interface GraphWorkflowEventLike {
  sequence?: number
  event_type: string
  node_name?: string | null
  public_payload?: Record<string, any>
}

export interface GraphWorkflowStep {
  key: string
  label: string
  status: GraphStepStatus
  summary: string
  details?: GraphWorkflowStepDetails
}

export interface GraphWorkflowStepDetails {
  sql?: string
  rows?: Array<Record<string, any>>
  columns?: string[]
  queries?: GraphWorkflowQueryDetails[]
}

export interface GraphWorkflowQueryDetails {
  title: string
  sql?: string
  rows?: Array<Record<string, any>>
  columns?: string[]
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
  dimensionValueFields: string[]
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
      label: graphNodeLabel(node),
      status: graphStepStatus(node, trace, pendingInteraction),
      summary: graphNodeSummary(node, pendingInteraction),
      details: graphNodeDetails(node),
    }))
}

export function buildGraphWorkflowStepsFromEvents(
  events: GraphWorkflowEventLike[],
  trace?: GraphTraceLike | null,
  pendingInteraction?: GraphPendingInteractionLike | null
): GraphWorkflowStep[] {
  const stepMap = new Map<string, GraphWorkflowStep>()
  for (const event of [...events].sort(
    (a, b) => Number(a.sequence || 0) - Number(b.sequence || 0)
  )) {
    if (event.event_type === 'run.resumed') {
      // 用户已提交补充信息后，前一个等待节点应从展示层退出等待态。
      for (const step of stepMap.values()) {
        if (step.status === 'waiting_input') step.status = 'succeeded'
      }
      continue
    }
    const nodeName = event.node_name || event.public_payload?.node_name
    if (!nodeName) continue
    const traceNode = trace?.nodes.find((node) => node.name === nodeName)
    const existing = stepMap.get(nodeName)
    const eventSummary = event.public_payload?.summary
    const eventNode: GraphTraceNodeLike = {
      name: nodeName,
      label:
        typeof event.public_payload?.label === 'string'
          ? event.public_payload.label
          : traceNode?.label,
      status: 'not_run',
      output: eventSummary ?? event.public_payload,
    }
    const nextStep: GraphWorkflowStep = existing || {
      key: nodeName,
      label: graphNodeLabel(eventNode),
      status: 'pending',
      summary: graphNodeSummary(eventNode, pendingInteraction),
      details: graphNodeDetails(eventNode),
    }
    if (eventNode.label) nextStep.label = eventNode.label
    if (event.event_type === 'node.started') nextStep.status = 'running'
    if (event.event_type === 'node.succeeded') nextStep.status = 'succeeded'
    if (event.event_type === 'node.failed') nextStep.status = 'failed'
    if (event.event_type === 'run.waiting_input') nextStep.status = 'waiting_input'
    const nextSummary = graphNodeSummary(eventNode, pendingInteraction)
    // 路由等技术事件不携带业务摘要，不能覆盖节点刚完成时已经展示的小字。
    if (nextSummary || !nextStep.summary) nextStep.summary = nextSummary
    const nextDetails = graphNodeDetails(eventNode)
    if (nextDetails) nextStep.details = nextDetails
    stepMap.set(nodeName, nextStep)
  }
  if (pendingInteraction?.status === 'pending' && !stepMap.has(pendingInteraction.node_name)) {
    stepMap.set(pendingInteraction.node_name, {
      key: pendingInteraction.node_name,
      label: graphNodeLabel({
        name: pendingInteraction.node_name,
        label: pendingInteraction.label,
        status: pendingInteraction.status,
      }),
      status: 'waiting_input',
      summary: pendingInteraction.prompt || '',
      details: undefined,
    })
  }
  return [...stepMap.values()]
}

export function buildVisibleGraphWorkflowSteps(
  trace: GraphTraceLike | null | undefined,
  pendingInteraction: GraphPendingInteractionLike | null | undefined,
  events: GraphWorkflowEventLike[] = [],
  loading = false
): GraphWorkflowStep[] {
  if (events.length || loading) {
    return buildGraphWorkflowStepsFromEvents(events, trace, pendingInteraction)
  }
  return buildGraphWorkflowSteps(trace, pendingInteraction)
}

export function graphProgressHeadline(
  trace?: GraphTraceLike | null,
  pendingInteraction?: GraphPendingInteractionLike | null,
  liveSteps: GraphWorkflowStep[] = []
) {
  const terminalHeadline = traceTerminalHeadline(trace)
  if (terminalHeadline) return terminalHeadline
  if (pendingInteraction?.status === 'pending') {
    return `需要你补充信息：${pendingInteraction.prompt || interactionTitle(pendingInteraction)}`
  }
  const activeLiveStep = [...liveSteps].reverse().find((step) => step.status === 'running')
  if (activeLiveStep) return `正在执行：${activeLiveStep.label}...`
  const failedLiveStep = [...liveSteps].reverse().find((step) => step.status === 'failed')
  if (failedLiveStep) return `执行失败：${failedLiveStep.label}`
  if (!trace) return '正在准备执行...'
  const runningNode =
    trace.nodes.find((node) => node.status === 'started') ||
    trace.nodes.find((node) => node.name === trace.current_node)
  if (runningNode) return `正在执行：${graphNodeLabel(runningNode)}...`
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
      dimensionValueFields: [],
    }
  }
  const targetSlots = interactionSlots(interaction)
  const schemaCard = interaction.response_schema?.['x-card']
  return {
    title: interactionTitle(interaction),
    prompt: interaction.prompt || interactionTitle(interaction),
    targetSlots,
    options: (interaction.options || []).map((option) => normalizeOption(option, targetSlots)),
    dimensionValueFields: Array.isArray(schemaCard?.dimension_value_fields)
      ? schemaCard.dimension_value_fields.map((field: unknown) => String(field)).filter(Boolean)
      : [],
  }
}

export function pendingInteractionFromGraphEvent(
  event?: GraphWorkflowEventLike | null
): GraphPendingInteractionLike | undefined {
  const pendingInteraction = event?.public_payload?.pending_interaction
  if (isPendingInteraction(pendingInteraction)) return pendingInteraction
  const summary = event?.public_payload?.summary
  if (isPendingInteraction(summary)) return summary
  return undefined
}

export function graphInteractionControlsDisabled(loading: boolean, waitingInput: boolean) {
  return loading && !waitingInput
}

export function graphLatestEventSequence(...eventGroups: GraphWorkflowEventLike[][]) {
  return eventGroups
    .flat()
    .filter((event) => !event.public_payload?.synthetic)
    .reduce((max, event) => Math.max(max, Number(event.sequence || 0)), 0)
}

export function graphInteractionOptionKey(option: NormalizedGraphInteractionOption, index: number) {
  const valueKey = stableInteractionValue(option.value ?? option.label)
  return `${index}:${option.slot}:${option.label}:${valueKey}`
}

export function buildGraphInteractionResponse(
  interaction: GraphPendingInteractionLike,
  option?: NormalizedGraphInteractionOption,
  customValue: string | Record<string, string> = '',
  skipped = false
): Record<string, any> {
  if (skipped) return { skipped: true }
  const fallbackSlot = interactionSlots(interaction)[0] || 'value'
  if (isDimensionValueMap(customValue)) {
    const dimensionValues = Object.fromEntries(
      Object.entries(customValue)
        .map(([key, value]) => [key, String(value || '').trim()])
        .filter(([key, value]) => Boolean(key && value))
    )
    if (Object.keys(dimensionValues).length) {
      const baseResponse =
        option?.value && typeof option.value === 'object' ? { ...option.value } : {}
      return {
        ...baseResponse,
        dimension_values: dimensionValues,
      }
    }
  }
  const trimmedCustom = typeof customValue === 'string' ? customValue.trim() : ''
  if (trimmedCustom) return { [fallbackSlot]: trimmedCustom }
  if (!option) return {}
  if (option.value && typeof option.value === 'object') return option.value
  return { [option.slot || fallbackSlot]: String(option.value ?? option.label) }
}

function isDimensionValueMap(value: unknown): value is Record<string, string> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

export function graphNodeLabel(node: GraphTraceNodeLike | string) {
  if (typeof node === 'string') return node
  return node.label || node.name
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
  if (node.status === 'waiting_input') {
    if (trace?.status === 'waiting_input' && trace.current_node === node.name)
      return 'waiting_input'
    return 'succeeded'
  }
  if (node.status === 'succeeded') return 'succeeded'
  if (node.status === 'failed') return 'failed'
  if (node.status === 'cancelled') return 'cancelled'
  if (trace?.current_node === node.name && trace.status !== 'succeeded') return 'running'
  return 'pending'
}

function traceTerminalHeadline(trace?: GraphTraceLike | null) {
  if (!trace) return ''
  const failedNode = trace.nodes.find((node) => node.status === 'failed')
  if (failedNode) return `执行失败：${graphNodeLabel(failedNode)}`
  if (trace.status === 'failed') return '执行失败'
  if (trace.status === 'cancelled') return '已停止'
  if (trace.status === 'succeeded') return '已完成'
  return ''
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
    return intentSummary(output)
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
  if (node.name === 'bind_query_plan') return queryPlanSummary(output)
  if (node.name === 'draw_image_profile')
    return output.profile || listText(output.chart_candidates) || ''
  if (node.name === 'generate_sql') return output.sql ? '已生成 SQL' : ''
  if (node.name === 'generate_split_queries') {
    const queryCount = Array.isArray(output.queries) ? output.queries.length : 0
    return queryCount ? `已生成 ${queryCount} 条 SQL` : ''
  }
  if (node.name === 'execute_sql') {
    const rowCount =
      output.row_count ?? output.rows_count ?? output.rows?.length ?? output.data?.length
    return rowCount !== undefined ? `返回 ${rowCount} 行` : ''
  }
  if (node.name === 'execute_split_queries') {
    const queries = Array.isArray(output.results)
      ? output.results
      : Array.isArray(output.rows)
        ? output.rows
        : []
    const rowCount = queries.reduce(
      (total, query) =>
        total + Number(query?.row_count ?? query?.sample_rows?.length ?? query?.rows?.length ?? 0),
      0
    )
    return queries.length ? `${queries.length} 条查询，共返回 ${rowCount} 行` : ''
  }
  if (node.name === 'handle_sql_error') return output.error || output.message || ''
  if (node.name === 'generate_question_answer') return output.answer || output.final_answer || ''
  if (node.name === 'recommend_questions')
    return listText(output.questions || output.recommendations)
  if (node.name === 'compose_final_reply')
    return output.answer || output.final_answer || output.content || ''
  return node.route_reason || ''
}

function graphNodeDetails(node: GraphTraceNodeLike): GraphWorkflowStepDetails | undefined {
  const output = objectOutput(node.output)
  if (node.name === 'generate_sql') {
    const sql = output.sql || output.query || output.generated_sql
    return sql ? { sql: String(sql) } : undefined
  }
  if (node.name === 'generate_split_queries') {
    const queries = normalizeSplitQueries(output.queries, 'sql')
    return queries.length ? { queries } : undefined
  }
  if (node.name === 'execute_sql') {
    const result = Array.isArray(output.results) ? output.results[0] : undefined
    const rows = normalizeRows(
      result?.sample_rows || output.rows || output.data || output.result || output.records
    )
    if (!rows.length) return undefined
    return {
      rows,
      columns: normalizeColumns(output.columns, rows),
    }
  }
  if (node.name === 'execute_split_queries') {
    const queries = Array.isArray(output.results)
      ? normalizeExecutionResults(output.results)
      : normalizeSplitQueries(output.rows, 'rows')
    return queries.length ? { queries } : undefined
  }
  return undefined
}

function normalizeExecutionResults(value: any): GraphWorkflowQueryDetails[] {
  if (!Array.isArray(value)) return []
  return value.map((result, index) => {
    const rows = normalizeRows(result?.sample_rows)
    return {
      title: String(result?.query_id || `查询 ${index + 1}`),
      rows,
      columns: normalizeColumns(result?.fields, rows),
    }
  })
}

function normalizeSplitQueries(value: any, mode: 'sql' | 'rows'): GraphWorkflowQueryDetails[] {
  if (!Array.isArray(value)) return []
  const queries: GraphWorkflowQueryDetails[] = []
  value.forEach((query) => {
    const title = listText(query?.metrics) || `模型 ${query?.model_id ?? '-'}`
    if (mode === 'sql') {
      const sql = String(query?.sql || '').trim()
      if (sql) queries.push({ title, sql })
      return
    }
    const rows = normalizeRows(query?.rows)
    queries.push({
      title,
      rows,
      columns: normalizeColumns(query?.columns, rows),
    })
  })
  return queries
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
  return interaction.prompt || '请补充信息'
}

function isPendingInteraction(value: any): value is GraphPendingInteractionLike {
  return (
    value &&
    typeof value === 'object' &&
    typeof value.interaction_id === 'string' &&
    typeof value.run_id === 'string' &&
    typeof value.node_name === 'string' &&
    value.status === 'pending'
  )
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

function intentSummary(output: Record<string, any>) {
  const parts = [
    output.intent_type ? `分析类型：${intentTypeLabel(output.intent_type)}` : '',
    listText(output.metric_mentions) ? `指标：${listText(output.metric_mentions)}` : '',
    dimensionSlotText(output.dimension_slots, output.dimension_mentions),
    timeRangeText(output.time_range, output.time_mentions),
    filterText(output.filter_mentions),
  ].filter(Boolean)
  return parts.join('；')
}

function intentTypeLabel(value: any) {
  const labels: Record<string, string> = {
    metric_query: '指标查询',
    trend_analysis: '趋势分析',
    ranking_analysis: '排行分析',
    comparison_analysis: '对比分析',
    detail_query: '明细查询',
    share_analysis: '占比分析',
    anomaly_analysis: '异常分析',
    unknown: '待确认',
  }
  return labels[String(value)] || String(value)
}

function queryPlanSummary(output: Record<string, any>) {
  const parts = [
    listText(output.metrics) ? `指标：${listText(output.metrics)}` : '',
    listText(output.group_bys) ? `维度：${listText(output.group_bys)}` : '',
    planFilterText(output.filters),
    planFilterText(output.having, '条件'),
    Array.isArray(output.sub_plans) && output.sub_plans.length
      ? `子查询：${output.sub_plans.length} 个`
      : '',
    output.status === 'infeasible' && output.infeasible_reason
      ? `不可行：${output.infeasible_reason}`
      : '',
  ].filter(Boolean)
  return parts.join('；') || output.strategy || output.status || ''
}

function planFilterText(filters: any, label = '筛选') {
  if (!Array.isArray(filters) || !filters.length) return ''
  const texts = filters
    .map((filter) => {
      if (!filter || typeof filter !== 'object') return ''
      const name = displayValue(filter) || filter.field || filter.dimension
      const operator = filter.operator || '='
      const value = filter.value
      if (!name && value === undefined) return ''
      if (value === undefined || value === null || value === '') return String(name)
      return `${name}${operator}${displayValue(value) || String(value)}`
    })
    .filter(Boolean)
  return texts.length ? `${label}：${texts.join('、')}` : ''
}

function dimensionSlotText(slots: any, mentions: any) {
  if (Array.isArray(slots) && slots.length) {
    const texts = slots
      .map((slot) => {
        const name = slot?.name || slot?.dimension
        if (!name) return ''
        if (
          slot.value_status === 'provided' &&
          slot.value !== undefined &&
          slot.value !== null &&
          slot.value !== ''
        ) {
          return `${name}=${slot.value}`
        }
        if (slot.role === 'group_by') return `按${name}分组`
        return `${name}（未提供具体值）`
      })
      .filter(Boolean)
    return texts.length ? `维度：${texts.join('、')}` : ''
  }
  const text = listText(mentions)
  return text ? `维度：${text}` : ''
}

function timeRangeText(timeRange: any, mentions: any) {
  if (
    timeRange &&
    typeof timeRange === 'object' &&
    timeRange.value_status === 'provided' &&
    timeRange.raw
  ) {
    return `时间：${timeRange.raw}`
  }
  const text = listText(mentions)
  return text ? `时间：${text}` : ''
}

function filterText(filters: any) {
  if (!Array.isArray(filters) || !filters.length) return ''
  const texts = filters
    .map((filter) => {
      if (!filter || typeof filter !== 'object') return ''
      const name = filter.name || filter.dimension || filter.field
      const value = filter.value
      if (!name && value === undefined) return ''
      if (value === undefined || value === null || value === '') return String(name)
      return `${name}=${value}`
    })
    .filter(Boolean)
  return texts.length ? `筛选：${texts.join('、')}` : ''
}

function normalizeRows(value: any): Array<Record<string, any>> {
  if (!Array.isArray(value)) return []
  return value
    .slice(0, 5)
    .map((row) => {
      if (row && typeof row === 'object' && !Array.isArray(row)) return row
      if (Array.isArray(row)) {
        return Object.fromEntries(row.map((cell, index) => [`列${index + 1}`, cell]))
      }
      return { 值: row }
    })
    .filter((row) => Object.keys(row).length)
}

function normalizeColumns(columns: any, rows: Array<Record<string, any>>) {
  if (Array.isArray(columns) && columns.length) {
    return columns.map((column) => displayValue(column) || String(column))
  }
  const keys = new Set<string>()
  rows.forEach((row) => Object.keys(row).forEach((key) => keys.add(key)))
  return [...keys].slice(0, 6)
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

function stableInteractionValue(value: any): string {
  if (!value || typeof value !== 'object') return String(value ?? '')
  if (Array.isArray(value))
    return `[${value.map((item) => stableInteractionValue(item)).join(',')}]`
  // 稳定序列化选项值，避免对象字段顺序变化导致同一选项无法匹配。
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${key}:${stableInteractionValue(value[key])}`)
    .join(',')}}`
}
