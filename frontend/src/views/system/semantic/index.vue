<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Connection,
  DataAnalysis,
  Delete,
  Document,
  Edit,
  Finished,
  MagicStick,
  Plus,
  Refresh,
  Search,
  View,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus-secondary'
import { headlessApi } from '@/api/headless'

type ActiveTab = 'domains' | 'models' | 'metrics' | 'dimensions' | 'datasets' | 'terms' | 'runtime'
type RuntimeTab = 'schema' | 'mapper'
type SimpleDialogType = 'domain' | 'metric' | 'dimension' | 'term'

const aggregateOptions = [
  { label: 'sum', value: 'SUM' },
  { label: 'max', value: 'MAX' },
  { label: 'min', value: 'MIN' },
  { label: 'avg', value: 'AVG' },
  { label: 'count', value: 'COUNT' },
  { label: 'count_distinct', value: 'COUNT_DISTINCT' },
  { label: 'None', value: 'NONE' },
]

const dimensionTypeOptions = [
  { label: '分类维度', value: 'categorical' },
  { label: '普通时间', value: 'time' },
  { label: '分区时间', value: 'partition_time' },
  { label: '主键', value: 'primary_key' },
  { label: '外键', value: 'foreign_key' },
]

const timeGranularityOptions = ['none', 'day', 'week', 'month', 'quarter', 'year']

const activeTab = ref<ActiveTab>('models')
const runtimeTab = ref<RuntimeTab>('schema')
const loading = ref(false)
const saveLoading = ref(false)
const schemaLoading = ref(false)
const mapperLoading = ref(false)
const tablesLoading = ref(false)
const fieldsLoading = ref(false)

const datasources = ref<any[]>([])
const domains = ref<any[]>([])
const models = ref<any[]>([])
const metrics = ref<any[]>([])
const dimensions = ref<any[]>([])
const datasets = ref<any[]>([])
const terms = ref<any[]>([])
const datasourceTables = ref<any[]>([])
const tableColumns = ref<any[]>([])
const fieldRows = ref<any[]>([])
const datasetSchema = ref<any>(null)
const mapResult = ref<any>(null)
const selectedDomainId = ref<number | string>('')
const selectedModelId = ref<number | string>('')
const selectedDatasetId = ref<number | string>('')
const mapperQuestion = ref('一号档口咨询UV是多少')

const simpleDialogVisible = ref(false)
const simpleDialogType = ref<SimpleDialogType>('domain')
const modelDialogVisible = ref(false)
const datasetDialogVisible = ref(false)
const measureInitDialogVisible = ref(false)
const simpleEditingId = ref<number | string>('')
const modelEditingId = ref<number | string>('')
const datasetEditingId = ref<number | string>('')
const measureInitModelId = ref<number | string>('')
const measureInitSelected = ref<Array<string>>([])

const simpleForm = reactive({
  domain_id: '',
  model_id: '',
  name: '',
  biz_name: '',
  description: '',
  alias_text: '',
  default_agg: 'SUM',
  dimension_type: 'categorical',
  semantic_type: '',
  metric_define_type: 'MEASURE',
  metric_expr: '',
  metric_filter_sql: '',
  metric_dependency_keys: [] as Array<number | string>,
})

const modelForm = reactive({
  domain_id: '',
  datasource_id: '',
  name: '',
  biz_name: '',
  description: '',
  alias_text: '',
  source_type: 'TABLE',
  table_name: '',
  sql: '',
  filter_sql: '',
  depends_text: '',
})

const datasetForm = reactive({
  domain_id: '',
  name: '',
  biz_name: '',
  description: '',
  alias_text: '',
  model_ids: [] as Array<number | string>,
})

const datasetConfigs = reactive<Record<string, any>>({})
const datasetMetricOptions = reactive<Record<string, any[]>>({})
const datasetDimensionOptions = reactive<Record<string, any[]>>({})

const currentDomain = computed(() => domains.value.find((item) => `${item.id}` === `${selectedDomainId.value}`))
const currentDataset = computed(() => datasets.value.find((item) => `${item.id}` === `${selectedDatasetId.value}`))
const displayedMetrics = computed(() =>
  selectedModelId.value ? metrics.value.filter((item) => `${item.model_id}` === `${selectedModelId.value}`) : metrics.value
)
const displayedDimensions = computed(() =>
  selectedModelId.value ? dimensions.value.filter((item) => `${item.model_id}` === `${selectedModelId.value}`) : dimensions.value
)
const selectedModel = computed(() => models.value.find((item) => `${item.id}` === `${selectedModelId.value}`))
const metricDisplayText = computed(() =>
  selectedModelId.value
    ? `当前展示：${selectedModel.value?.name || '选中模型'}，共 ${displayedMetrics.value.length} / 总 ${metrics.value.length} 条`
    : `当前展示：全部模型，共 ${metrics.value.length} 条`
)
const dimensionDisplayText = computed(() =>
  selectedModelId.value
    ? `当前展示：${selectedModel.value?.name || '选中模型'}，共 ${displayedDimensions.value.length} / 总 ${dimensions.value.length} 条`
    : `当前展示：全部模型，共 ${dimensions.value.length} 条`
)

const schemaStats = computed(() => ({
  metrics: datasetSchema.value?.metrics?.length || 0,
  dimensions: datasetSchema.value?.dimensions?.length || 0,
  values: datasetSchema.value?.dimension_values?.length || 0,
  terms: datasetSchema.value?.terms?.length || 0,
}))

const simpleDialogTitle = computed(() => {
  const titleMap: Record<SimpleDialogType, string> = {
    domain: '主题域',
    metric: '指标',
    dimension: '维度',
    term: '术语',
  }
  return `${simpleEditingId.value ? '编辑' : '新建'}${titleMap[simpleDialogType.value]}`
})

const modelDialogTitle = computed(() => `${modelEditingId.value ? '编辑' : '新建'}模型`)
const datasetDialogTitle = computed(() => `${datasetEditingId.value ? '编辑' : '新建'}数据集`)
const metricFormModel = computed(() => models.value.find((item) => `${item.id}` === `${simpleForm.model_id}`))
const metricMeasureOptions = computed(() => metricFormModel.value?.model_detail?.measures || [])
const metricFieldOptions = computed(() => metricFormModel.value?.model_detail?.fields || [])
const metricMetricOptions = computed(() =>
  metrics.value.filter((item) => `${item.model_id}` === `${simpleForm.model_id}` && `${item.id}` !== `${simpleEditingId.value}`)
)
const measureInitModel = computed(() => models.value.find((item) => `${item.id}` === `${measureInitModelId.value}`))
const measureInitMeasures = computed(() => measureInitModel.value?.model_detail?.measures || [])
const measureInitExistingBizNames = computed(
  () =>
    new Set(
      metrics.value
        .filter((item) => `${item.model_id}` === `${measureInitModelId.value}`)
        .map((item) => String(item.biz_name))
    )
)
const measureInitRows = computed(() =>
  measureInitMeasures.value
    .map((item: any) => {
      const bizName = item.bizName || item.biz_name
      return {
        ...item,
        bizName,
        created: measureInitExistingBizNames.value.has(String(bizName)),
      }
    })
    .filter((item: any) => item.bizName)
)
const metricDependencyLabel = computed(() => {
  const labelMap: Record<string, string> = {
    MEASURE: '度量',
    FIELD: '字段',
    METRIC: '指标',
  }
  return labelMap[simpleForm.metric_define_type] || '依赖项'
})

onMounted(async () => {
  await loadAll()
})

const loadAll = async () => {
  loading.value = true
  try {
    const [dsRes, domainRes] = await Promise.all([headlessApi.datasourceList(), headlessApi.domainList()])
    datasources.value = Array.isArray(dsRes) ? dsRes : []
    domains.value = Array.isArray(domainRes) ? domainRes : []
    if (!selectedDomainId.value && domains.value.length) selectedDomainId.value = domains.value[0].id
    await loadScopedAssets()
  } finally {
    loading.value = false
  }
}

const loadScopedAssets = async () => {
  const domainParams = selectedDomainId.value ? { domain_id: selectedDomainId.value } : undefined
  const [modelRes, datasetRes, termRes] = await Promise.all([
    headlessApi.modelList(domainParams),
    headlessApi.datasetList(domainParams),
    headlessApi.termList(domainParams),
  ])
  models.value = Array.isArray(modelRes) ? modelRes : []
  datasets.value = Array.isArray(datasetRes) ? datasetRes : []
  terms.value = Array.isArray(termRes) ? termRes : []
  if (!models.value.some((item) => `${item.id}` === `${selectedModelId.value}`)) {
    selectedModelId.value = ''
  }
  if (!datasets.value.some((item) => `${item.id}` === `${selectedDatasetId.value}`)) {
    selectedDatasetId.value = datasets.value[0]?.id || ''
  }
  await loadModelAssets()
}

const loadModelAssets = async () => {
  const [metricRes, dimensionRes] = await Promise.all([headlessApi.metricList(), headlessApi.dimensionList()])
  const scopedModelIds = new Set(models.value.map((item) => String(item.id)))
  metrics.value = Array.isArray(metricRes) ? metricRes.filter((item) => scopedModelIds.has(String(item.model_id))) : []
  dimensions.value = Array.isArray(dimensionRes) ? dimensionRes.filter((item) => scopedModelIds.has(String(item.model_id))) : []
}

const handleDomainChange = async () => {
  selectedModelId.value = ''
  selectedDatasetId.value = ''
  datasetSchema.value = null
  mapResult.value = null
  await loadScopedAssets()
}

const splitText = (value: string) =>
  value
    ? value
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
    : []

const validateRequired = (items: Array<{ label: string; value: any; type?: 'input' | 'select' }>) => {
  const missing = items.find((item) => {
    if (Array.isArray(item.value)) return item.value.length === 0
    return item.value === undefined || item.value === null || `${item.value}`.trim() === ''
  })
  if (missing) {
    ElMessage.warning(`${missing.type === 'select' ? '请选择' : '请填写'}${missing.label}`)
    return false
  }
  return true
}

const resetSimpleForm = () => {
  Object.assign(simpleForm, {
    domain_id: selectedDomainId.value,
    model_id: selectedModelId.value,
    name: '',
    biz_name: '',
    description: '',
    alias_text: '',
    default_agg: 'SUM',
    dimension_type: 'categorical',
    semantic_type: '',
    metric_define_type: 'MEASURE',
    metric_expr: '',
    metric_filter_sql: '',
    metric_dependency_keys: [],
  })
}

const openSimpleDialog = (type: SimpleDialogType) => {
  resetSimpleForm()
  simpleDialogType.value = type
  simpleEditingId.value = ''
  if (type === 'metric' && !simpleForm.model_id && models.value.length) {
    simpleForm.model_id = models.value[0].id
  }
  simpleDialogVisible.value = true
}

const resetMeasureInitSelection = () => {
  measureInitSelected.value = measureInitRows.value.filter((item: any) => !item.created).map((item: any) => String(item.bizName))
}

const openMeasureInitDialog = () => {
  if (!models.value.length) {
    ElMessage.warning('请先创建模型')
    return
  }
  measureInitModelId.value = selectedModelId.value || models.value[0].id
  resetMeasureInitSelection()
  measureInitDialogVisible.value = true
}

const handleMeasureInitModelChange = () => {
  resetMeasureInitSelection()
}

const isMeasureInitChecked = (bizName: string) => measureInitSelected.value.includes(String(bizName))

const toggleMeasureInitRow = (bizName: string, checked: boolean) => {
  const key = String(bizName)
  if (checked && !measureInitSelected.value.includes(key)) {
    measureInitSelected.value = [...measureInitSelected.value, key]
  } else if (!checked) {
    measureInitSelected.value = measureInitSelected.value.filter((item) => item !== key)
  }
}

const selectPendingMeasures = () => {
  resetMeasureInitSelection()
}

const saveMeasureInitMetrics = async () => {
  if (!measureInitModelId.value) {
    ElMessage.warning('请选择模型')
    return
  }
  if (!measureInitSelected.value.length) {
    ElMessage.warning('请选择要生成指标的度量')
    return
  }
  saveLoading.value = true
  try {
    const res = await headlessApi.metricBatchCreateFromMeasures({
      model_id: Number(measureInitModelId.value),
      measure_biz_names: measureInitSelected.value,
    })
    const createdCount = res?.created?.length || 0
    const skippedCount = res?.skipped?.length || 0
    ElMessage.success(`已生成 ${createdCount} 个指标${skippedCount ? `，跳过 ${skippedCount} 个` : ''}`)
    measureInitDialogVisible.value = false
    selectedModelId.value = measureInitModelId.value
    await loadModelAssets()
  } finally {
    saveLoading.value = false
  }
}

const metricDependencyKeysFromParams = (defineType: string, params: any) => {
  if (defineType === 'FIELD') {
    const fields = params?.metricDefineByFieldParams?.fields || params?.fields || []
    return fields.map((item: any) => item.fieldName || item.field_name).filter(Boolean)
  }
  if (defineType === 'METRIC') {
    const sourceMetrics = params?.metricDefineByMetricParams?.metrics || params?.metrics || []
    return sourceMetrics.map((item: any) => item.id || item.bizName || item.biz_name).filter(Boolean)
  }
  const measures = params?.metricDefineByMeasureParams?.measures || params?.measures || []
  return measures.map((item: any) => item.bizName || item.biz_name).filter(Boolean)
}

const metricExprFromParams = (defineType: string, params: any) => {
  if (defineType === 'FIELD') return params?.metricDefineByFieldParams?.expr || params?.expr || ''
  if (defineType === 'METRIC') return params?.metricDefineByMetricParams?.expr || params?.expr || ''
  return params?.metricDefineByMeasureParams?.expr || params?.expr || ''
}

const metricFilterSqlFromParams = (defineType: string, params: any) => {
  if (defineType === 'FIELD') return params?.metricDefineByFieldParams?.filterSql || params?.filterSql || ''
  if (defineType === 'METRIC') return params?.metricDefineByMetricParams?.filterSql || params?.filterSql || ''
  return params?.metricDefineByMeasureParams?.filterSql || params?.filterSql || ''
}

const openSimpleEditDialog = (type: SimpleDialogType, row: any) => {
  resetSimpleForm()
  simpleDialogType.value = type
  simpleEditingId.value = row.id
  const metricTypeParams = row.type_params || {}
  const metricDefineType = metricTypeParams.metricDefineType || row.define_type || 'MEASURE'
  Object.assign(simpleForm, {
    domain_id: row.domain_id || selectedDomainId.value,
    model_id: row.model_id || selectedModelId.value,
    name: row.name || '',
    biz_name: row.biz_name || '',
    description: row.description || '',
    alias_text: (row.alias || []).join('，'),
    default_agg: row.default_agg || 'SUM',
    dimension_type: row.type || 'categorical',
    semantic_type: row.semantic_type || '',
    metric_define_type: metricDefineType,
    metric_expr: metricExprFromParams(metricDefineType, metricTypeParams),
    metric_filter_sql: metricFilterSqlFromParams(metricDefineType, metricTypeParams),
    metric_dependency_keys: metricDependencyKeysFromParams(metricDefineType, metricTypeParams),
  })
  simpleDialogVisible.value = true
}

const handleMetricDefineTypeChange = () => {
  simpleForm.metric_dependency_keys = []
  simpleForm.metric_expr = ''
  simpleForm.metric_filter_sql = ''
}

const selectedMetricMeasures = () => {
  const selected = new Set(simpleForm.metric_dependency_keys.map((item) => String(item)))
  return metricMeasureOptions.value
    .filter((item: any) => selected.has(String(item.bizName)))
    .map((item: any) => ({
      name: item.name,
      bizName: item.bizName,
      expr: item.expr,
      agg: item.agg,
      constraint: item.constraint || '',
    }))
}

const selectedMetricFields = () => {
  const selected = new Set(simpleForm.metric_dependency_keys.map((item) => String(item)))
  return metricFieldOptions.value
    .filter((item: any) => selected.has(String(item.fieldName)))
    .map((item: any) => ({
      fieldName: item.fieldName,
      dataType: item.dataType,
      name: item.name,
      bizName: item.bizName,
    }))
}

const selectedMetricMetrics = () => {
  const selected = new Set(simpleForm.metric_dependency_keys.map((item) => String(item)))
  return metricMetricOptions.value
    .filter((item: any) => selected.has(String(item.id)) || selected.has(String(item.biz_name)))
    .map((item: any) => ({
      id: item.id,
      name: item.name,
      bizName: item.biz_name,
    }))
}

const buildMetricTypeParams = () => {
  const base = {
    expr: simpleForm.metric_expr,
    filterSql: simpleForm.metric_filter_sql,
  }
  if (simpleForm.metric_define_type === 'FIELD') {
    return {
      metricDefineType: 'FIELD',
      metricDefineByFieldParams: {
        ...base,
        fields: selectedMetricFields(),
      },
    }
  }
  if (simpleForm.metric_define_type === 'METRIC') {
    return {
      metricDefineType: 'METRIC',
      metricDefineByMetricParams: {
        ...base,
        metrics: selectedMetricMetrics(),
      },
    }
  }
  return {
    metricDefineType: 'MEASURE',
    metricDefineByMeasureParams: {
      ...base,
      measures: selectedMetricMeasures(),
    },
  }
}

const buildSimplePayload = () => {
  if (simpleDialogType.value === 'domain') {
    return {
      name: simpleForm.name,
      biz_name: simpleForm.biz_name,
      admin: '',
    }
  }
  if (simpleDialogType.value === 'metric') {
    return {
      model_id: Number(simpleForm.model_id),
      name: simpleForm.name,
      biz_name: simpleForm.biz_name,
      description: simpleForm.description,
      alias: splitText(simpleForm.alias_text),
      default_agg: simpleForm.default_agg,
      define_type: simpleForm.metric_define_type,
      type_params: buildMetricTypeParams(),
    }
  }
  if (simpleDialogType.value === 'dimension') {
    return {
      model_id: Number(simpleForm.model_id),
      name: simpleForm.name,
      biz_name: simpleForm.biz_name,
      description: simpleForm.description,
      type: simpleForm.dimension_type,
      semantic_type: simpleForm.semantic_type || null,
      alias: splitText(simpleForm.alias_text),
    }
  }
  return {
    domain_id: Number(simpleForm.domain_id),
    name: simpleForm.name,
    alias: splitText(simpleForm.alias_text),
    description: simpleForm.description,
  }
}

const saveSimpleEntity = async () => {
  const requiredItems = [
    ...(simpleDialogType.value === 'term'
      ? [{ label: '主题域', value: simpleForm.domain_id, type: 'select' as const }]
      : simpleDialogType.value === 'domain'
        ? []
        : [{ label: '模型', value: simpleForm.model_id, type: 'select' as const }]),
    { label: '名称', value: simpleForm.name },
    ...(simpleDialogType.value === 'term' ? [] : [{ label: '英文标识', value: simpleForm.biz_name }]),
    ...(simpleDialogType.value === 'metric'
      ? [
          { label: metricDependencyLabel.value, value: simpleForm.metric_dependency_keys, type: 'select' as const },
          { label: '指标表达式', value: simpleForm.metric_expr },
        ]
      : []),
  ]
  if (!validateRequired(requiredItems)) {
    return
  }
  saveLoading.value = true
  try {
    const payload = buildSimplePayload()
    if (simpleEditingId.value) {
      if (simpleDialogType.value === 'domain') await headlessApi.domainUpdate(simpleEditingId.value, payload)
      else if (simpleDialogType.value === 'metric') await headlessApi.metricUpdate(simpleEditingId.value, payload)
      else if (simpleDialogType.value === 'dimension') await headlessApi.dimensionUpdate(simpleEditingId.value, payload)
      else await headlessApi.termUpdate(simpleEditingId.value, payload)
    } else {
      if (simpleDialogType.value === 'domain') await headlessApi.domainCreate(payload)
      else if (simpleDialogType.value === 'metric') await headlessApi.metricCreate(payload)
      else if (simpleDialogType.value === 'dimension') await headlessApi.dimensionCreate(payload)
      else await headlessApi.termCreate(payload)
    }
    ElMessage.success('保存成功')
    simpleDialogVisible.value = false
    await loadAll()
  } finally {
    saveLoading.value = false
  }
}

const openModelDialog = () => {
  modelEditingId.value = ''
  Object.assign(modelForm, {
    domain_id: selectedDomainId.value,
    datasource_id: '',
    name: '',
    biz_name: '',
    description: '',
    alias_text: '',
    source_type: 'TABLE',
    table_name: '',
    sql: '',
    filter_sql: '',
    depends_text: '',
  })
  datasourceTables.value = []
  tableColumns.value = []
  fieldRows.value = []
  modelDialogVisible.value = true
}

const fieldsFromModelDetail = (detail: any = {}) => {
  const dimensionsByBizName = new Map((detail.dimensions || []).map((item: any) => [item.bizName, item]))
  const measuresByBizName = new Map((detail.measures || []).map((item: any) => [item.bizName, item]))
  const identifiersByBizName = new Map((detail.identifiers || []).map((item: any) => [item.bizName, item]))
  return (detail.fields || []).map((field: any) => {
    const bizName = field.bizName || field.fieldName
    const measure: any = measuresByBizName.get(bizName)
    const dimension: any = dimensionsByBizName.get(bizName)
    const identifier: any = identifiersByBizName.get(bizName)
    const asset = measure || dimension || identifier
    // 按 Headless 模型详情反推字段角色，编辑模型时保留原有指标/维度语义。
    const role = measure ? 'MEASURE' : identifier ? 'IDENTIFIER' : dimension ? 'DIMENSION' : 'FIELD'
    return normalizeBuildField({
      field_name: field.fieldName,
      data_type: field.dataType,
      name: field.name,
      biz_name: bizName,
      expr: asset?.expr || field.expr || field.fieldName,
      role,
      alias: asset?.alias || [],
      default_agg: measure?.agg || '',
      semantic_type: dimension?.semanticType || '',
      dimension_type: dimension?.type || (identifier?.type === 'foreign' ? 'foreign_key' : identifier ? 'primary_key' : 'categorical'),
      identifier_type: identifier?.type || (dimension?.type === 'foreign_key' ? 'foreign' : 'primary'),
      date_format: dimension?.dateFormat || 'yyyy-MM-dd',
      time_granularity: dimension?.typeParams?.timeGranularity || 'day',
      time_primary: dimension?.typeParams?.isPrimary !== 'false',
      create_asset: !!asset,
    })
  })
}

const openModelEditDialog = (row: any) => {
  modelEditingId.value = row.id
  const detail = row.model_detail || {}
  Object.assign(modelForm, {
    domain_id: row.domain_id || selectedDomainId.value,
    datasource_id: row.datasource_id || '',
    name: row.name || '',
    biz_name: row.biz_name || '',
    description: row.description || '',
    alias_text: (row.alias || []).join('，'),
    source_type: row.source_type || 'TABLE',
    table_name: detail.tableQuery?.table || '',
    sql: detail.sqlQuery?.sql || '',
    filter_sql: row.filter_sql || '',
    depends_text: JSON.stringify(row.depends || [], null, 2),
  })
  tableColumns.value = []
  fieldRows.value = fieldsFromModelDetail(detail)
  datasourceTables.value = []
  if (modelForm.datasource_id) {
    tablesLoading.value = true
    headlessApi
      .datasourceTables(modelForm.datasource_id)
      .then((res: any) => {
        datasourceTables.value = Array.isArray(res) ? res : []
      })
      .finally(() => {
        tablesLoading.value = false
      })
  }
  modelDialogVisible.value = true
}

const loadDatasourceTables = async () => {
  modelForm.table_name = ''
  tableColumns.value = []
  fieldRows.value = []
  if (!modelForm.datasource_id) return
  tablesLoading.value = true
  try {
    const res = await headlessApi.datasourceTables(modelForm.datasource_id)
    datasourceTables.value = Array.isArray(res) ? res : []
  } finally {
    tablesLoading.value = false
  }
}

const loadTableColumns = async () => {
  if (!modelForm.datasource_id || !modelForm.table_name) {
    ElMessage.warning('请选择数据源和表')
    return
  }
  fieldsLoading.value = true
  try {
    const columns = await headlessApi.datasourceColumns(modelForm.datasource_id, modelForm.table_name)
    tableColumns.value = Array.isArray(columns) ? columns : []
    const schema = await headlessApi.modelBuildSchema({
      datasource_id: Number(modelForm.datasource_id),
      source_type: modelForm.source_type,
      table_name: modelForm.table_name,
      columns: tableColumns.value,
    })
    fieldRows.value = (schema.fields || []).map(normalizeBuildField)
  } finally {
    fieldsLoading.value = false
  }
}

const normalizeBuildField = (item: any) => ({
  field_name: item.field_name,
  data_type: item.data_type,
  name: item.name,
  biz_name: item.biz_name,
  expr: item.expr || item.field_name,
  role: item.role,
  alias_text: (item.alias || []).join('，'),
  default_agg: item.default_agg || (item.role === 'MEASURE' ? 'SUM' : ''),
  semantic_type: item.semantic_type || '',
  dimension_type: item.dimension_type || (item.semantic_type === 'time' ? 'partition_time' : item.role === 'IDENTIFIER' ? 'primary_key' : 'categorical'),
  identifier_type: item.identifier_type || 'primary',
  date_format: item.date_format || 'yyyy-MM-dd',
  time_granularity: item.time_granularity || 'day',
  time_primary: item.time_primary !== false,
  create_asset: item.create_asset !== false,
})

const addManualField = () => {
  fieldRows.value.push({
    field_name: '',
    data_type: 'VARCHAR',
    name: '',
    biz_name: '',
    expr: '',
    role: 'DIMENSION',
    alias_text: '',
    default_agg: '',
    semantic_type: '',
    dimension_type: 'categorical',
    identifier_type: 'primary',
    date_format: 'yyyy-MM-dd',
    time_granularity: 'day',
    time_primary: true,
    create_asset: true,
  })
}

const removeField = (index: number) => {
  fieldRows.value.splice(index, 1)
}

const handleFieldRoleChange = (row: any, role: string) => {
  row.role = role
  if (role === 'MEASURE') {
    row.default_agg = row.default_agg || 'SUM'
    row.semantic_type = ''
    row.dimension_type = 'categorical'
    row.create_asset = true
  } else if (role === 'FIELD') {
    row.default_agg = ''
    row.semantic_type = ''
    row.create_asset = false
  } else if (role === 'IDENTIFIER') {
    row.default_agg = ''
    row.identifier_type = row.identifier_type || 'primary'
    row.dimension_type = row.identifier_type === 'foreign' ? 'foreign_key' : 'primary_key'
    row.create_asset = true
  } else {
    row.default_agg = ''
    row.dimension_type = row.dimension_type || 'categorical'
    row.create_asset = true
  }
}

const semanticItemLabel = (row: any) => {
  if (row.role === 'MEASURE') return '纳入度量'
  if (row.role === 'DIMENSION') return '生成维度'
  if (row.role === 'IDENTIFIER') return '纳入标识'
  return '普通字段'
}

const buildModelDetail = () => ({
  queryType: modelForm.source_type === 'SQL' ? 'sql_query' : 'table_query',
  tableQuery: modelForm.table_name ? { table: modelForm.table_name } : {},
  sqlQuery: modelForm.sql ? { sql: modelForm.sql } : {},
  fields: fieldRows.value.map((item) => ({
    fieldName: item.field_name,
    dataType: item.data_type,
    name: item.name || item.field_name,
    bizName: item.biz_name || item.field_name,
    expr: item.expr || item.field_name,
  })),
  identifiers: fieldRows.value
    .filter((item) => item.role === 'IDENTIFIER' && item.create_asset)
    .map((item) => ({
      name: item.name || item.field_name,
      bizName: item.biz_name || item.field_name,
      fieldName: item.field_name,
      type: item.dimension_type === 'foreign_key' ? 'foreign' : item.identifier_type || 'primary',
    })),
  dimensions: fieldRows.value
    .filter((item) => ['IDENTIFIER', 'DIMENSION'].includes(item.role) && item.create_asset)
    .map((item) => {
      const dimensionType =
        item.role === 'IDENTIFIER'
          ? ['primary_key', 'foreign_key'].includes(item.dimension_type)
            ? item.dimension_type
            : `${item.identifier_type || 'primary'}_key`
          : item.dimension_type || 'categorical'
      return {
        name: item.name || item.field_name,
        bizName: item.biz_name || item.field_name,
        expr: item.expr || item.field_name,
        dataType: item.data_type,
        type: dimensionType,
        semanticType: ['time', 'partition_time'].includes(dimensionType) ? 'time' : item.semantic_type || null,
        dateFormat: ['time', 'partition_time'].includes(dimensionType) ? item.date_format || 'yyyy-MM-dd' : undefined,
        typeParams: ['time', 'partition_time'].includes(dimensionType)
          ? {
              isPrimary: item.time_primary === false ? 'false' : 'true',
              timeGranularity: item.time_granularity || 'day',
            }
          : undefined,
        alias: splitText(item.alias_text),
        createDimension: true,
      }
    }),
  measures: fieldRows.value
    .filter((item) => item.role === 'MEASURE' && item.create_asset)
    .map((item) => ({
      name: item.name || item.field_name,
      bizName: item.biz_name || item.field_name,
      expr: item.expr || item.field_name,
      agg: item.default_agg || 'SUM',
      datasourceId: Number(modelEditingId.value || 0) || undefined,
      alias: splitText(item.alias_text),
      isCreateMetric: 0,
      createMetric: false,
    })),
  sqlVariables: [],
})

const parseModelDepends = () => {
  const text = modelForm.depends_text.trim()
  if (!text) return []
  try {
    const parsed = JSON.parse(text)
    if (!Array.isArray(parsed)) {
      ElMessage.warning('模型依赖必须是 JSON 数组')
      return null
    }
    return parsed
  } catch {
    ElMessage.warning('模型依赖 JSON 格式错误')
    return null
  }
}

const sanitizeModelBizName = (value: string) =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')

const buildInternalModelBizName = () => {
  if (modelEditingId.value && modelForm.biz_name) return modelForm.biz_name
  const sourceName = modelForm.source_type === 'TABLE' ? modelForm.table_name : modelForm.name
  const baseName = sanitizeModelBizName(sourceName) || `model_${Date.now().toString(36)}`
  const exists = models.value.some(
    (item) =>
      `${item.domain_id}` === `${modelForm.domain_id}` &&
      `${item.id}` !== `${modelEditingId.value}` &&
      item.biz_name === baseName
  )
  // 英文标识只作为系统内部唯一键，用户不再需要手动维护。
  return exists ? `${baseName}_${Date.now().toString(36)}` : baseName
}

const saveModelWizard = async () => {
  const requiredItems = [
    { label: '主题域', value: modelForm.domain_id, type: 'select' as const },
    { label: '数据源', value: modelForm.datasource_id, type: 'select' as const },
    { label: '名称', value: modelForm.name },
    ...(modelForm.source_type === 'TABLE'
      ? [{ label: '表', value: modelForm.table_name, type: 'select' as const }]
      : [{ label: 'SQL', value: modelForm.sql }]),
  ]
  if (!validateRequired(requiredItems)) {
    return
  }
  if (!fieldRows.value.length) {
    ElMessage.warning('请先生成或添加字段')
    return
  }
  saveLoading.value = true
  try {
    const depends = parseModelDepends()
    if (depends === null) return
    const payload = {
      domain_id: Number(modelForm.domain_id),
      datasource_id: Number(modelForm.datasource_id),
      name: modelForm.name,
      biz_name: buildInternalModelBizName(),
      description: modelForm.description,
      alias: splitText(modelForm.alias_text),
      source_type: modelForm.source_type,
      table_name: modelForm.table_name || null,
      sql: modelForm.sql || null,
      filter_sql: modelForm.filter_sql || null,
      depends,
      model_detail: buildModelDetail(),
    }
    if (modelEditingId.value) {
      const updated = await headlessApi.modelUpdate(modelEditingId.value, payload)
      selectedModelId.value = updated?.id || selectedModelId.value
      ElMessage.success('模型已更新')
    } else {
      const res = await headlessApi.modelCreateWithAssets(payload)
      selectedModelId.value = res?.model?.id || selectedModelId.value
      ElMessage.success('模型和维度已生成，指标可在指标页单独定义')
    }
    modelDialogVisible.value = false
    await loadAll()
  } finally {
    saveLoading.value = false
  }
}

const openDatasetDialog = () => {
  datasetEditingId.value = ''
  Object.assign(datasetForm, {
    domain_id: selectedDomainId.value,
    name: '',
    biz_name: '',
    description: '',
    alias_text: '',
    model_ids: models.value.map((item) => item.id),
  })
  Object.keys(datasetConfigs).forEach((key) => delete datasetConfigs[key])
  Object.keys(datasetMetricOptions).forEach((key) => delete datasetMetricOptions[key])
  Object.keys(datasetDimensionOptions).forEach((key) => delete datasetDimensionOptions[key])
  datasetForm.model_ids.forEach((id) => {
    datasetConfigs[String(id)] = { includesAll: true, metrics: [], dimensions: [] }
    loadDatasetModelAssets(id)
  })
  datasetDialogVisible.value = true
}

const resetDatasetAssetCaches = () => {
  Object.keys(datasetConfigs).forEach((key) => delete datasetConfigs[key])
  Object.keys(datasetMetricOptions).forEach((key) => delete datasetMetricOptions[key])
  Object.keys(datasetDimensionOptions).forEach((key) => delete datasetDimensionOptions[key])
}

const openDatasetEditDialog = (row: any) => {
  datasetEditingId.value = row.id
  const configs = row.data_set_detail?.dataSetModelConfigs || []
  Object.assign(datasetForm, {
    domain_id: row.domain_id || selectedDomainId.value,
    name: row.name || '',
    biz_name: row.biz_name || '',
    description: row.description || '',
    alias_text: (row.alias || []).join('，'),
    model_ids: configs.map((item: any) => item.id),
  })
  resetDatasetAssetCaches()
  configs.forEach((config: any) => {
    datasetConfigs[String(config.id)] = {
      includesAll: config.includesAll !== false,
      metrics: config.metrics || [],
      dimensions: config.dimensions || [],
    }
    loadDatasetModelAssets(config.id)
  })
  datasetDialogVisible.value = true
}

const handleDatasetModelsChange = async () => {
  const selected = new Set(datasetForm.model_ids.map((id) => String(id)))
  Object.keys(datasetConfigs).forEach((key) => {
    if (!selected.has(key)) delete datasetConfigs[key]
  })
  for (const id of datasetForm.model_ids) {
    const key = String(id)
    if (!datasetConfigs[key]) datasetConfigs[key] = { includesAll: true, metrics: [], dimensions: [] }
    await loadDatasetModelAssets(id)
  }
}

const loadDatasetModelAssets = async (modelId: number | string) => {
  const key = String(modelId)
  if (datasetMetricOptions[key] && datasetDimensionOptions[key]) return
  const [metricRes, dimensionRes] = await Promise.all([
    headlessApi.metricList({ model_id: modelId }),
    headlessApi.dimensionList({ model_id: modelId }),
  ])
  datasetMetricOptions[key] = Array.isArray(metricRes) ? metricRes : []
  datasetDimensionOptions[key] = Array.isArray(dimensionRes) ? dimensionRes : []
}

const saveDatasetDialog = async () => {
  if (
    !validateRequired([
      { label: '主题域', value: datasetForm.domain_id, type: 'select' },
      { label: '名称', value: datasetForm.name },
      { label: '英文标识', value: datasetForm.biz_name },
      { label: '模型', value: datasetForm.model_ids, type: 'select' },
    ])
  ) {
    return
  }
  saveLoading.value = true
  try {
    const payload = {
      domain_id: Number(datasetForm.domain_id),
      name: datasetForm.name,
      biz_name: datasetForm.biz_name,
      description: datasetForm.description,
      alias: splitText(datasetForm.alias_text),
      data_set_detail: {
        dataSetModelConfigs: datasetForm.model_ids.map((id) => {
          const config = datasetConfigs[String(id)] || {}
          return {
            id: Number(id),
            includesAll: !!config.includesAll,
            metrics: config.includesAll ? [] : (config.metrics || []).map(Number),
            dimensions: config.includesAll ? [] : (config.dimensions || []).map(Number),
            tagIds: [],
          }
        }),
      },
      query_config: {},
    }
    if (datasetEditingId.value) {
      await headlessApi.datasetUpdate(datasetEditingId.value, payload)
      ElMessage.success('数据集已更新')
    } else {
      await headlessApi.datasetCreate(payload)
      ElMessage.success('数据集已创建')
    }
    datasetDialogVisible.value = false
    await loadAll()
  } finally {
    saveLoading.value = false
  }
}

const previewSchema = async () => {
  if (!selectedDatasetId.value) {
    ElMessage.warning('请选择数据集')
    return
  }
  schemaLoading.value = true
  try {
    datasetSchema.value = await headlessApi.datasetSchema(selectedDatasetId.value)
  } finally {
    schemaLoading.value = false
  }
}

const runMapper = async () => {
  if (!selectedDatasetId.value || !mapperQuestion.value) {
    ElMessage.warning('请选择数据集并输入问题')
    return
  }
  mapperLoading.value = true
  try {
    mapResult.value = await headlessApi.schemaMap({
      query_text: mapperQuestion.value,
      dataset_ids: [selectedDatasetId.value],
    })
  } finally {
    mapperLoading.value = false
  }
}

const rebuildKnowledge = async () => {
  if (!selectedDatasetId.value) {
    ElMessage.warning('请选择数据集')
    return
  }
  await headlessApi.knowledgeRebuild(selectedDatasetId.value)
  ElMessage.success('知识索引已重建')
}

const deleteEntity = async (type: SimpleDialogType | 'model' | 'dataset', row: any) => {
  const nameMap: Record<string, string> = {
    domain: '主题域',
    model: '模型',
    metric: '指标',
    dimension: '维度',
    dataset: '数据集',
    term: '术语',
  }
  const action = await ElMessageBox.confirm(`确认删除${nameMap[type]}「${row.name}」？`, '删除确认', {
    confirmButtonType: 'danger',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    customClass: 'confirm-no_icon',
  }).catch(() => null)
  if (action !== 'confirm') return

  if (type === 'domain') await headlessApi.domainDelete(row.id)
  else if (type === 'model') await headlessApi.modelDelete(row.id)
  else if (type === 'metric') await headlessApi.metricDelete(row.id)
  else if (type === 'dimension') await headlessApi.dimensionDelete(row.id)
  else if (type === 'dataset') await headlessApi.datasetDelete(row.id)
  else await headlessApi.termDelete(row.id)

  ElMessage.success('删除成功')
  if (`${row.id}` === `${selectedDomainId.value}`) selectedDomainId.value = ''
  if (`${row.id}` === `${selectedModelId.value}`) selectedModelId.value = ''
  if (`${row.id}` === `${selectedDatasetId.value}`) selectedDatasetId.value = ''
  datasetSchema.value = null
  mapResult.value = null
  await loadAll()
}

const formatJson = (value: any) => JSON.stringify(value || {}, null, 2)
const aliasText = (row: any) => (row.alias || []).join('、') || '-'
const datasourceName = (id: number | string) => datasources.value.find((item) => `${item.id}` === `${id}`)?.name || id
const modelName = (id: number | string) => models.value.find((item) => `${item.id}` === `${id}`)?.name || id
const modelBizName = (id: number | string) => models.value.find((item) => `${item.id}` === `${id}`)?.biz_name || '-'
</script>

<template>
  <div class="headless-page">
    <div class="headless-header">
      <div>
        <div class="breadcrumb">工作区 / Headless BI</div>
        <h2>语义资产</h2>
        <p>Domain -> Model -> Metric / Dimension -> DataSet -> DataSetSchema</p>
      </div>
      <div class="header-actions">
        <el-select v-model="selectedDomainId" class="wide-select" clearable placeholder="主题域" @change="handleDomainChange">
          <el-option v-for="item in domains" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <div class="summary-row">
      <div class="summary-main">
        <span>当前主题域</span>
        <strong>{{ currentDomain?.name || '未选择' }}</strong>
        <p>{{ currentDomain?.biz_name || '创建主题域后配置模型与数据集。' }}</p>
      </div>
      <div class="summary-item"><span>模型</span><strong>{{ models.length }}</strong></div>
      <div class="summary-item"><span>指标</span><strong>{{ metrics.length }}</strong></div>
      <div class="summary-item"><span>维度</span><strong>{{ dimensions.length }}</strong></div>
      <div class="summary-item"><span>数据集</span><strong>{{ datasets.length }}</strong></div>
    </div>

    <div class="workbench">
      <div class="tabs-row">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="主题域" name="domains" />
          <el-tab-pane label="模型" name="models" />
          <el-tab-pane label="指标" name="metrics" />
          <el-tab-pane label="维度" name="dimensions" />
          <el-tab-pane label="数据集" name="datasets" />
          <el-tab-pane label="术语" name="terms" />
          <el-tab-pane label="运行时验证" name="runtime" />
        </el-tabs>
      </div>

      <div v-if="activeTab === 'domains'" class="table-area">
        <div class="toolbar"><el-button type="primary" :icon="Plus" @click="openSimpleDialog('domain')">新建主题域</el-button></div>
        <el-table :data="domains" height="calc(100vh - 390px)">
          <el-table-column prop="name" label="主题域" min-width="180" />
          <el-table-column prop="biz_name" label="英文标识" min-width="180" />
          <el-table-column prop="admin" label="管理员" min-width="160" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" :icon="Edit" @click="openSimpleEditDialog('domain', row)">编辑</el-button>
                <el-button link type="danger" :icon="Delete" @click="deleteEntity('domain', row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="activeTab === 'models'" class="table-area">
        <div class="toolbar">
          <el-select v-model="selectedModelId" class="wide-select" clearable placeholder="模型筛选">
            <el-option v-for="item in models" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-button type="primary" :icon="Connection" @click="openModelDialog">新建模型</el-button>
        </div>
        <el-table :data="models" height="calc(100vh - 390px)">
          <el-table-column prop="name" label="模型" min-width="180" />
          <el-table-column label="数据源" min-width="160">
            <template #default="{ row }">{{ datasourceName(row.datasource_id) }}</template>
          </el-table-column>
          <el-table-column prop="source_type" label="来源类型" width="110" />
          <el-table-column label="表 / SQL" min-width="180">
            <template #default="{ row }">{{ row.model_detail?.tableQuery?.table || row.model_detail?.sqlQuery?.sql || '-' }}</template>
          </el-table-column>
          <el-table-column label="字段数" width="90">
            <template #default="{ row }">{{ row.model_detail?.fields?.length || 0 }}</template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="220" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" :icon="Edit" @click="openModelEditDialog(row)">编辑</el-button>
                <el-button link type="danger" :icon="Delete" @click="deleteEntity('model', row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="activeTab === 'metrics'" class="table-area">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-select v-model="selectedModelId" class="wide-select" clearable placeholder="全部模型">
              <el-option v-for="item in models" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <span class="toolbar-meta">{{ metricDisplayText }}</span>
          </div>
          <div class="toolbar-actions">
            <el-button :icon="MagicStick" @click="openMeasureInitDialog">从度量生成指标</el-button>
            <el-button type="primary" :icon="Plus" @click="openSimpleDialog('metric')">新建指标</el-button>
          </div>
        </div>
        <el-table :data="displayedMetrics" height="calc(100vh - 390px)">
          <el-table-column prop="name" label="指标" min-width="180" />
          <el-table-column prop="biz_name" label="英文标识" min-width="180" />
          <el-table-column label="所属模型" min-width="180">
            <template #default="{ row }">
              <div class="model-cell">
                <strong>{{ modelName(row.model_id) }}</strong>
                <span>{{ modelBizName(row.model_id) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="default_agg" label="默认聚合" width="120" />
          <el-table-column prop="define_type" label="定义方式" width="120" />
          <el-table-column label="别名" min-width="180"><template #default="{ row }">{{ aliasText(row) }}</template></el-table-column>
          <el-table-column prop="description" label="口径描述" min-width="240" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" :icon="Edit" @click="openSimpleEditDialog('metric', row)">编辑</el-button>
                <el-button link type="danger" :icon="Delete" @click="deleteEntity('metric', row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="activeTab === 'dimensions'" class="table-area">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-select v-model="selectedModelId" class="wide-select" clearable placeholder="全部模型">
              <el-option v-for="item in models" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <span class="toolbar-meta">{{ dimensionDisplayText }}</span>
          </div>
          <el-button type="primary" :icon="Plus" @click="openSimpleDialog('dimension')">新建维度</el-button>
        </div>
        <el-table :data="displayedDimensions" height="calc(100vh - 390px)">
          <el-table-column prop="name" label="维度" min-width="180" />
          <el-table-column prop="biz_name" label="英文标识" min-width="180" />
          <el-table-column label="所属模型" min-width="180">
            <template #default="{ row }">
              <div class="model-cell">
                <strong>{{ modelName(row.model_id) }}</strong>
                <span>{{ modelBizName(row.model_id) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="维度类型" width="130" />
          <el-table-column prop="semantic_type" label="语义类型" width="130" />
          <el-table-column label="维值映射" width="110"><template #default="{ row }">{{ row.dim_value_maps?.length || 0 }}</template></el-table-column>
          <el-table-column label="别名" min-width="180"><template #default="{ row }">{{ aliasText(row) }}</template></el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" :icon="Edit" @click="openSimpleEditDialog('dimension', row)">编辑</el-button>
                <el-button link type="danger" :icon="Delete" @click="deleteEntity('dimension', row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="activeTab === 'datasets'" class="table-area">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-button type="primary" :icon="Finished" @click="openDatasetDialog">新建数据集</el-button>
          </div>
        </div>
        <el-table :data="datasets" height="calc(100vh - 390px)">
          <el-table-column prop="name" label="数据集" min-width="180" />
          <el-table-column prop="biz_name" label="英文标识" min-width="180" />
          <el-table-column label="模型配置" min-width="220">
            <template #default="{ row }">
              <el-tag v-for="item in row.data_set_detail?.dataSetModelConfigs || []" :key="item.id" class="tag-gap">
                {{ modelName(item.id) }} {{ item.includesAll ? '全部' : '部分' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="别名" min-width="180"><template #default="{ row }">{{ aliasText(row) }}</template></el-table-column>
          <el-table-column prop="description" label="描述" min-width="240" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" :icon="Edit" @click="openDatasetEditDialog(row)">编辑</el-button>
                <el-button link type="danger" :icon="Delete" @click="deleteEntity('dataset', row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="activeTab === 'terms'" class="table-area">
        <div class="toolbar"><el-button type="primary" :icon="Plus" @click="openSimpleDialog('term')">新建术语</el-button></div>
        <el-table :data="terms" height="calc(100vh - 390px)">
          <el-table-column prop="name" label="术语" min-width="180" />
          <el-table-column label="别名" min-width="180"><template #default="{ row }">{{ aliasText(row) }}</template></el-table-column>
          <el-table-column label="关联指标" width="110"><template #default="{ row }">{{ row.related_metrics?.length || 0 }}</template></el-table-column>
          <el-table-column label="关联维度" width="110"><template #default="{ row }">{{ row.related_dimensions?.length || 0 }}</template></el-table-column>
          <el-table-column prop="description" label="描述" min-width="240" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button link type="primary" :icon="Edit" @click="openSimpleEditDialog('term', row)">编辑</el-button>
                <el-button link type="danger" :icon="Delete" @click="deleteEntity('term', row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="activeTab === 'runtime'" class="schema-area">
        <div class="runtime-header">
          <div class="toolbar-left">
            <el-select v-model="selectedDatasetId" class="wide-select" clearable placeholder="验证数据集">
              <el-option v-for="item in datasets" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
            <el-segmented
              v-model="runtimeTab"
              :options="[
                { label: 'Schema 预览', value: 'schema' },
                { label: 'Schema Mapping', value: 'mapper' },
              ]"
            />
          </div>
          <el-button :icon="MagicStick" @click="rebuildKnowledge">重建知识索引</el-button>
        </div>
        <div v-if="runtimeTab === 'schema'" class="runtime-panel">
          <div class="toolbar">
            <div class="toolbar-left">
              <el-button type="primary" :icon="View" :loading="schemaLoading" @click="previewSchema">生成 Schema</el-button>
              <span class="toolbar-meta">{{ currentDataset?.name || '请选择数据集' }}</span>
            </div>
          </div>
          <div class="schema-grid">
            <section class="schema-section">
              <div class="section-title"><DataAnalysis /><span>数据集结构</span></div>
              <div class="schema-stats">
                <div><span>指标</span><strong>{{ schemaStats.metrics }}</strong></div>
                <div><span>维度</span><strong>{{ schemaStats.dimensions }}</strong></div>
                <div><span>维值</span><strong>{{ schemaStats.values }}</strong></div>
                <div><span>术语</span><strong>{{ schemaStats.terms }}</strong></div>
              </div>
              <p class="muted">{{ currentDataset?.name || '请选择数据集后生成 Schema。' }}</p>
            </section>
            <section class="schema-section">
              <div class="section-title"><Document /><span>DataSetSchema JSON</span></div>
              <pre class="json-preview">{{ formatJson(datasetSchema) }}</pre>
            </section>
          </div>
        </div>
        <div v-else class="runtime-panel">
          <div class="mapper-bar">
            <el-input v-model="mapperQuestion" class="mapper-input" :prefix-icon="Search" placeholder="输入口语化问题" @keyup.enter="runMapper" />
            <el-button type="primary" :icon="Search" :loading="mapperLoading" @click="runMapper">映射</el-button>
          </div>
          <pre class="json-preview mapper-preview">{{ formatJson(mapResult) }}</pre>
        </div>
      </div>
    </div>

    <el-dialog v-model="modelDialogVisible" :title="modelDialogTitle" width="1180px" top="5vh">
      <div class="model-builder">
        <section class="builder-section">
          <div class="section-title"><Connection /><span>模型来源</span></div>
          <el-form class="model-source-form" label-position="top">
            <div class="model-source-grid">
              <el-form-item class="required-item" label="主题域">
                <el-select v-model="modelForm.domain_id">
                  <el-option v-for="item in domains" :key="item.id" :label="item.name" :value="item.id" />
                </el-select>
              </el-form-item>
              <el-form-item class="required-item" label="数据源">
                <el-select v-model="modelForm.datasource_id" filterable @change="loadDatasourceTables">
                  <el-option v-for="item in datasources" :key="item.id" :label="item.name" :value="item.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="来源类型">
                <el-segmented v-model="modelForm.source_type" :options="['TABLE', 'SQL']" />
              </el-form-item>
              <el-form-item label="模型过滤">
                <el-input v-model="modelForm.filter_sql" placeholder="where ..." />
              </el-form-item>
              <el-form-item class="required-item" label="名称">
                <el-input v-model="modelForm.name" />
              </el-form-item>
              <el-form-item label="别名">
                <el-input v-model="modelForm.alias_text" />
              </el-form-item>
              <el-form-item label="描述">
                <el-input v-model="modelForm.description" />
              </el-form-item>
            </div>
            <el-form-item v-if="modelForm.source_type === 'TABLE'" class="table-picker-row required-item" label="表">
              <div class="inline-field">
                <el-select v-model="modelForm.table_name" filterable :loading="tablesLoading" placeholder="选择表">
                  <el-option
                    v-for="item in datasourceTables"
                    :key="item.table_name"
                    :label="item.table_name"
                    :value="item.table_name"
                  />
                </el-select>
                <el-button :icon="Refresh" :loading="fieldsLoading" @click="loadTableColumns">拉取字段</el-button>
              </div>
            </el-form-item>
            <el-form-item v-else class="sql-editor-row required-item" label="SQL">
              <el-input v-model="modelForm.sql" type="textarea" :rows="4" />
            </el-form-item>
            <el-form-item class="sql-editor-row" label="模型依赖">
              <el-input
                v-model="modelForm.depends_text"
                type="textarea"
                :rows="3"
                placeholder='[{"modelId":2,"joinType":"left","leftKey":"seller_id","rightKey":"seller_id"}]'
              />
            </el-form-item>
          </el-form>
        </section>

        <section class="builder-section">
          <div class="section-title">
            <DataAnalysis />
            <span>字段分类</span>
            <el-button class="section-action" :icon="Plus" @click="addManualField">添加字段</el-button>
          </div>
          <el-table :data="fieldRows" height="360px">
            <el-table-column label="字段" min-width="150">
              <template #default="{ row }"><el-input v-model="row.field_name" /></template>
            </el-table-column>
            <el-table-column label="类型" width="130">
              <template #default="{ row }"><el-input v-model="row.data_type" /></template>
            </el-table-column>
            <el-table-column label="名称" min-width="150">
              <template #default="{ row }"><el-input v-model="row.name" /></template>
            </el-table-column>
            <el-table-column label="英文标识" min-width="150">
              <template #default="{ row }"><el-input v-model="row.biz_name" /></template>
            </el-table-column>
            <el-table-column label="角色" width="150">
              <template #default="{ row }">
                <el-select v-model="row.role" @change="(value: string) => handleFieldRoleChange(row, value)">
                  <el-option label="标识" value="IDENTIFIER" />
                  <el-option label="维度" value="DIMENSION" />
                  <el-option label="度量" value="MEASURE" />
                  <el-option label="普通字段" value="FIELD" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="聚合函数" width="140">
              <template #default="{ row }">
                <el-select v-model="row.default_agg" :disabled="row.role !== 'MEASURE'" placeholder="选择">
                  <el-option v-for="item in aggregateOptions" :key="item.value || 'none'" :label="item.label" :value="item.value" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="维度类型" width="140">
              <template #default="{ row }">
                <el-select v-model="row.dimension_type" :disabled="['MEASURE', 'FIELD'].includes(row.role)" placeholder="选择">
                  <el-option v-for="item in dimensionTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="时间粒度" width="120">
              <template #default="{ row }">
                <el-select v-model="row.time_granularity" :disabled="!['time', 'partition_time'].includes(row.dimension_type)" placeholder="粒度">
                  <el-option v-for="item in timeGranularityOptions" :key="item" :label="item" :value="item" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="日期格式" width="130">
              <template #default="{ row }">
                <el-input v-model="row.date_format" :disabled="!['time', 'partition_time'].includes(row.dimension_type)" />
              </template>
            </el-table-column>
            <el-table-column label="别名" min-width="160">
              <template #default="{ row }"><el-input v-model="row.alias_text" /></template>
            </el-table-column>
            <el-table-column label="语义项" width="130">
              <template #default="{ row }">
                <div class="semantic-switch-cell">
                  <el-switch v-model="row.create_asset" :disabled="row.role === 'FIELD'" />
                  <span>{{ semanticItemLabel(row) }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80">
              <template #default="{ $index }"><el-button link type="danger" @click="removeField($index)">删除</el-button></template>
            </el-table-column>
          </el-table>
        </section>
      </div>
      <template #footer>
        <el-button @click="modelDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saveLoading" @click="saveModelWizard">保存模型</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="datasetDialogVisible" :title="datasetDialogTitle" width="960px">
      <el-form label-position="top">
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item class="required-item" label="主题域">
              <el-select v-model="datasetForm.domain_id">
                <el-option v-for="item in domains" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8"><el-form-item class="required-item" label="名称"><el-input v-model="datasetForm.name" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item class="required-item" label="英文标识"><el-input v-model="datasetForm.biz_name" /></el-form-item></el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item label="别名"><el-input v-model="datasetForm.alias_text" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="描述"><el-input v-model="datasetForm.description" /></el-form-item></el-col>
        </el-row>
        <el-form-item class="required-item" label="模型">
          <el-select v-model="datasetForm.model_ids" multiple filterable @change="handleDatasetModelsChange">
            <el-option v-for="item in models" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="dataset-configs">
        <section v-for="modelId in datasetForm.model_ids" :key="modelId" class="dataset-config">
          <div class="config-head">
            <strong>{{ modelName(modelId) }}</strong>
            <el-switch v-model="datasetConfigs[String(modelId)].includesAll" active-text="全部暴露" inactive-text="手动选择" />
          </div>
          <div v-if="!datasetConfigs[String(modelId)].includesAll" class="asset-selectors">
            <el-form label-position="top">
              <el-form-item label="指标">
                <el-select v-model="datasetConfigs[String(modelId)].metrics" multiple filterable>
                  <el-option v-for="item in datasetMetricOptions[String(modelId)] || []" :key="item.id" :label="item.name" :value="item.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="维度">
                <el-select v-model="datasetConfigs[String(modelId)].dimensions" multiple filterable>
                  <el-option v-for="item in datasetDimensionOptions[String(modelId)] || []" :key="item.id" :label="item.name" :value="item.id" />
                </el-select>
              </el-form-item>
            </el-form>
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="datasetDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saveLoading" @click="saveDatasetDialog">保存数据集</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="measureInitDialogVisible" title="从度量生成指标" width="820px">
      <div class="measure-init">
        <div class="measure-init-toolbar">
          <el-select v-model="measureInitModelId" class="wide-select" placeholder="选择模型" @change="handleMeasureInitModelChange">
            <el-option v-for="item in models" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-button @click="selectPendingMeasures">选择待生成</el-button>
          <span class="toolbar-meta">已选 {{ measureInitSelected.length }} / {{ measureInitRows.length }}</span>
        </div>
        <el-table :data="measureInitRows" height="360px">
          <el-table-column width="56">
            <template #default="{ row }">
              <el-checkbox
                :disabled="row.created"
                :model-value="isMeasureInitChecked(row.bizName)"
                @change="(checked: boolean) => toggleMeasureInitRow(row.bizName, checked)"
              />
            </template>
          </el-table-column>
          <el-table-column label="度量" min-width="170">
            <template #default="{ row }">
              <div class="model-cell">
                <strong>{{ row.name || row.bizName }}</strong>
                <span>{{ row.bizName }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="expr" label="表达式" min-width="160" />
          <el-table-column prop="agg" label="聚合函数" width="120" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.created ? 'info' : 'success'">{{ row.created ? '已有指标' : '待生成' }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="measureInitDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saveLoading" @click="saveMeasureInitMetrics">生成指标</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="simpleDialogVisible" :title="simpleDialogTitle" width="680px">
      <el-form label-position="top">
        <el-row v-if="simpleDialogType !== 'domain'" :gutter="12">
          <el-col v-if="simpleDialogType === 'term'" :span="12">
            <el-form-item class="required-item" label="主题域">
              <el-select v-model="simpleForm.domain_id">
                <el-option v-for="item in domains" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-else :span="12">
            <el-form-item class="required-item" label="模型">
              <el-select v-model="simpleForm.model_id">
                <el-option v-for="item in models" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12"><el-form-item class="required-item" label="名称"><el-input v-model="simpleForm.name" /></el-form-item></el-col>
          <el-col v-if="simpleDialogType !== 'term'" :span="12"><el-form-item class="required-item" label="英文标识"><el-input v-model="simpleForm.biz_name" /></el-form-item></el-col>
        </el-row>
        <el-form-item v-if="simpleDialogType !== 'domain'" label="别名">
          <el-input v-model="simpleForm.alias_text" placeholder="多个别名用逗号或换行分隔" />
        </el-form-item>
        <el-form-item v-if="simpleDialogType !== 'domain'" label="描述">
          <el-input v-model="simpleForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <template v-if="simpleDialogType === 'metric'">
          <div class="metric-define-panel">
            <div class="metric-define-head">
              <strong>指标定义</strong>
              <span>从模型度量、字段或已有指标中选择依赖项，并编写计算表达式。</span>
            </div>
            <el-row :gutter="12">
              <el-col :span="8">
                <el-form-item class="required-item" label="定义方式">
                  <el-select v-model="simpleForm.metric_define_type" @change="handleMetricDefineTypeChange">
                    <el-option label="按度量" value="MEASURE" />
                    <el-option label="按字段" value="FIELD" />
                    <el-option label="按已有指标" value="METRIC" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="默认聚合">
                  <el-select v-model="simpleForm.default_agg" :disabled="simpleForm.metric_define_type !== 'MEASURE'" placeholder="选择">
                    <el-option v-for="item in aggregateOptions" :key="item.value" :label="item.label" :value="item.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="过滤条件">
                  <el-input v-model="simpleForm.metric_filter_sql" placeholder="status = 'paid'" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item v-if="simpleForm.metric_define_type === 'MEASURE'" class="required-item" label="度量">
              <el-select v-model="simpleForm.metric_dependency_keys" multiple filterable placeholder="选择模型中的度量">
                <el-option
                  v-for="item in metricMeasureOptions"
                  :key="item.bizName"
                  :label="`${item.name || item.bizName} / ${item.bizName}`"
                  :value="item.bizName"
                />
              </el-select>
            </el-form-item>
            <el-form-item v-if="simpleForm.metric_define_type === 'FIELD'" class="required-item" label="字段">
              <el-select v-model="simpleForm.metric_dependency_keys" multiple filterable placeholder="选择模型字段">
                <el-option
                  v-for="item in metricFieldOptions"
                  :key="item.fieldName"
                  :label="`${item.name || item.fieldName} / ${item.fieldName}`"
                  :value="item.fieldName"
                />
              </el-select>
            </el-form-item>
            <el-form-item v-if="simpleForm.metric_define_type === 'METRIC'" class="required-item" label="已有指标">
              <el-select v-model="simpleForm.metric_dependency_keys" multiple filterable placeholder="选择已有指标">
                <el-option
                  v-for="item in metricMetricOptions"
                  :key="item.id"
                  :label="`${item.name} / ${item.biz_name}`"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item class="required-item" label="指标表达式">
              <el-input
                v-model="simpleForm.metric_expr"
                type="textarea"
                :rows="3"
                :placeholder="
                  simpleForm.metric_define_type === 'FIELD'
                    ? '字段不带聚合函数，例如 sum(amount) 或 count(distinct user_id)'
                    : '度量/指标已完成聚合，例如 visit_uv / pay_uv'
                "
              />
            </el-form-item>
          </div>
        </template>
        <template v-if="simpleDialogType === 'dimension'">
          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="维度类型"><el-input v-model="simpleForm.dimension_type" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="语义类型"><el-input v-model="simpleForm.semantic_type" /></el-form-item></el-col>
          </el-row>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="simpleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saveLoading" @click="saveSimpleEntity">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped lang="less">
.headless-page {
  min-height: 100%;
  padding: 24px;
  background: #f4f7fb;
  color: #1f2633;
}

.headless-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 16px;

  h2 {
    margin: 12px 0 6px;
    font-size: 24px;
  }

  p,
  .breadcrumb {
    margin: 0;
    color: #6b7280;
    font-size: 13px;
  }
}

.header-actions,
.toolbar,
.toolbar-actions,
.toolbar-left,
.measure-init-toolbar,
.mapper-bar,
.inline-field,
.config-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.wide-select {
  width: 220px;
}

.summary-row {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) repeat(4, minmax(120px, 160px));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-main,
.summary-item,
.workbench,
.schema-section,
.builder-section,
.dataset-config {
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  background: #fff;
}

.summary-main,
.summary-item {
  padding: 16px;

  span,
  p {
    margin: 0;
    color: #6b7280;
    font-size: 13px;
  }

  strong {
    display: block;
    margin: 8px 0 4px;
    font-size: 22px;
  }
}

.summary-item strong {
  font-size: 28px;
}

.workbench {
  overflow: hidden;
}

.tabs-row {
  padding: 0 16px;
  border-bottom: 1px solid #e6ebf2;
}

.table-area,
.schema-area {
  padding: 16px;
}

.toolbar {
  justify-content: space-between;
  margin-bottom: 12px;
}

.runtime-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  background: #f8fafc;
}

.runtime-panel {
  min-width: 0;
}

.toolbar-meta {
  color: #6b7280;
  font-size: 13px;
}

.measure-init {
  display: grid;
  gap: 12px;
}

.model-cell {
  display: grid;
  gap: 2px;

  strong {
    color: #1f2633;
    font-weight: 600;
  }

  span {
    color: #8b95a5;
    font-size: 12px;
  }
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
  white-space: nowrap;
}

.row-actions :deep(.ed-button) {
  height: 28px;
  margin-left: 0;
  padding: 0;
  line-height: 28px;
}

.row-actions :deep(.ed-button + .ed-button) {
  margin-left: 0;
}

.metric-define-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  background: #f8fafc;
}

.metric-define-head {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;

  strong {
    color: #1f2633;
    font-weight: 700;
  }

  span {
    color: #6b7280;
    font-size: 13px;
  }
}

.semantic-switch-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  white-space: nowrap;

  span {
    color: #6b7280;
    font-size: 12px;
  }
}

.schema-grid,
.model-builder {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
}

.model-builder {
  grid-template-columns: 1fr;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 0 14px;
  border-bottom: 1px solid #edf1f6;
  font-weight: 700;
}

.section-title > svg {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
}

.section-action {
  margin-left: auto;
}

.builder-section :deep(.ed-form) {
  padding: 14px;
}

.builder-section {
  min-width: 0;
  overflow: hidden;
}

.model-source-form {
  overflow: hidden;
}

.model-source-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 16px;
  align-items: start;
}

.model-source-grid :deep(.ed-form-item) {
  min-width: 0;
  margin-bottom: 8px;
}

.required-item :deep(.ed-form-item__label)::before {
  content: '*';
  margin-right: 4px;
  color: #f56c6c;
}

.model-source-grid :deep(.ed-select),
.model-source-grid :deep(.ed-input),
.table-picker-row :deep(.ed-select),
.sql-editor-row :deep(.ed-textarea) {
  width: 100%;
}

.table-picker-row,
.sql-editor-row {
  max-width: 100%;
  margin-top: 8px;
}

.inline-field {
  max-width: 520px;
  flex-wrap: nowrap;
}

.inline-field :deep(.ed-select) {
  flex: 1 1 auto;
  min-width: 0;
}

.schema-stats {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding: 14px;

  span {
    display: block;
    color: #6b7280;
    font-size: 13px;
  }

  strong {
    font-size: 24px;
  }
}

.muted {
  margin: 0;
  padding: 0 14px 14px;
  color: #6b7280;
}

.mapper-input {
  max-width: 560px;
}

.json-preview {
  margin: 0;
  padding: 12px;
  min-height: 320px;
  max-height: calc(100vh - 430px);
  overflow: auto;
  background: #111827;
  color: #f8fafc;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
}

.mapper-preview {
  margin-top: 12px;
}

.dataset-configs {
  display: grid;
  gap: 12px;
  max-height: 420px;
  overflow: auto;
}

.dataset-config {
  padding: 12px;
}

.config-head {
  justify-content: space-between;
}

.asset-selectors {
  margin-top: 12px;
}

.tag-gap {
  margin-right: 6px;
}

:deep(.ed-tabs__header) {
  margin-bottom: 0;
}

:deep(.ed-table th.ed-table__cell) {
  background: #f6f8fb;
  color: #5f6877;
  font-weight: 700;
}

@media (max-width: 1180px) {
  .headless-header,
  .header-actions {
    display: block;
  }

  .header-actions {
    margin-top: 12px;
  }

  .summary-row,
  .schema-grid {
    grid-template-columns: 1fr;
  }
}
</style>
