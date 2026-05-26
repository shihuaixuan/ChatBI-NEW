<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, Close, Edit, MagicStick, MoreFilled, Plus, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus-secondary'
import { datasourceApi } from '@/api/datasource'
import { semanticApi, type ValidateExpressionPayload } from '@/api/semantic'

type AssetType = 'METRIC' | 'DIMENSION'
type AssetStatus = 'CANDIDATE' | 'APPROVED' | 'DISABLED' | 'DEPRECATED'
type ActiveTab = 'metrics' | 'dimensions' | 'candidates'

interface PageState {
  currentPage: number
  pageSize: number
  total: number
}

const activeTab = ref<ActiveTab>('metrics')
const datasourceId = ref<number | string>('')
const datasourceOptions = ref<any[]>([])
const tableOptions = ref<any[]>([])
const fieldOptions = ref<any[]>([])
const metrics = ref<any[]>([])
const dimensions = ref<any[]>([])
const candidateRows = ref<any[]>([])
const selectedCandidates = ref<any[]>([])
const loading = ref(false)
const initLoading = ref(false)
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const drawerAssetType = ref<AssetType>('METRIC')
const validateLoading = ref(false)
const saveLoading = ref(false)
const validateResult = ref<any>(null)
const candidateValidateResult = reactive<Record<string, any>>({})

const filterForm = reactive({
  keyword: '',
  status: '',
  table_id: '',
})

const metricsPage = reactive<PageState>({
  currentPage: 1,
  pageSize: 10,
  total: 0,
})

const dimensionsPage = reactive<PageState>({
  currentPage: 1,
  pageSize: 10,
  total: 0,
})

const candidatesPage = reactive<PageState>({
  currentPage: 1,
  pageSize: 10,
  total: 0,
})

const overviewStats = reactive({
  approvedMetrics: 0,
  approvedDimensions: 0,
  candidateMetrics: 0,
  candidateDimensions: 0,
})

const emptyForm = {
  id: '',
  datasource_id: '',
  table_id: '',
  field_id: '',
  name: '',
  display_name: '',
  aliases_text: '',
  description: '',
  expr: '',
  define_type: 'MEASURE',
  default_agg: 'SUM',
  filter_sql: '',
  data_type: '',
  data_format: '',
  default_time_dimension_id: '',
  related_dimension_ids_text: '',
  dimension_type: 'CATEGORY',
  semantic_type: 'UNKNOWN',
  time_granularities_text: '',
  default_values_text: '',
  owner_id: '',
  status: 'CANDIDATE' as AssetStatus,
}

const assetForm = reactive({ ...emptyForm })

const selectedDatasource = computed(() => {
  return datasourceOptions.value.find((item) => `${item.id}` === `${datasourceId.value}`)
})

const currentPageState = computed(() => {
  if (activeTab.value === 'metrics') return metricsPage
  if (activeTab.value === 'dimensions') return dimensionsPage
  return candidatesPage
})

const pagedCandidates = computed(() => {
  const start = (candidatesPage.currentPage - 1) * candidatesPage.pageSize
  return candidateRows.value.slice(start, start + candidatesPage.pageSize)
})

const candidateTotal = computed(() => overviewStats.candidateMetrics + overviewStats.candidateDimensions)

const tableNameById = computed(() => {
  return tableOptions.value.reduce((map, item) => {
    map[`${item.id}`] = item.table_name || item.tableName
    return map
  }, {} as Record<string, string>)
})

const fieldNameById = computed(() => {
  return fieldOptions.value.reduce((map, item) => {
    map[`${item.id}`] = item.field_name || item.fieldName
    return map
  }, {} as Record<string, string>)
})

onMounted(async () => {
  await loadDatasources()
})

const loadDatasources = async () => {
  const res = await datasourceApi.list()
  datasourceOptions.value = Array.isArray(res) ? res : []
  if (!datasourceId.value && datasourceOptions.value.length) {
    datasourceId.value = datasourceOptions.value[0].id
  }
  if (datasourceId.value) {
    await handleDatasourceChange()
  }
}

const handleDatasourceChange = async () => {
  filterForm.keyword = ''
  filterForm.status = ''
  filterForm.table_id = ''
  metricsPage.currentPage = 1
  dimensionsPage.currentPage = 1
  candidatesPage.currentPage = 1
  await loadTables()
  await loadOverviewStats()
  await search()
}

const loadTables = async () => {
  if (!datasourceId.value) {
    tableOptions.value = []
    return
  }
  const res = await datasourceApi.tableList(Number(datasourceId.value))
  tableOptions.value = Array.isArray(res) ? res : []
}

const loadFields = async (tableId?: number | string) => {
  if (!tableId) {
    fieldOptions.value = []
    return
  }
  const res = await datasourceApi.fieldList(Number(tableId))
  fieldOptions.value = Array.isArray(res) ? res : []
}

const buildPageParams = (status?: string) => {
  const params: any = {
    datasource_id: datasourceId.value,
  }
  if (filterForm.keyword) params.keyword = filterForm.keyword
  if (status ?? filterForm.status) params.status = status ?? filterForm.status
  if (filterForm.table_id) params.table_id = filterForm.table_id
  return params
}

const buildOverviewParams = (status: AssetStatus) => ({
  datasource_id: datasourceId.value,
  status,
})

const loadOverviewStats = async () => {
  if (!datasourceId.value) return
  const [approvedMetricRes, approvedDimensionRes, candidateMetricRes, candidateDimensionRes] =
    await Promise.all([
      semanticApi.metricPage(1, 1, buildOverviewParams('APPROVED')),
      semanticApi.dimensionPage(1, 1, buildOverviewParams('APPROVED')),
      semanticApi.metricPage(1, 1, buildOverviewParams('CANDIDATE')),
      semanticApi.dimensionPage(1, 1, buildOverviewParams('CANDIDATE')),
    ])
  overviewStats.approvedMetrics = approvedMetricRes?.total_count || 0
  overviewStats.approvedDimensions = approvedDimensionRes?.total_count || 0
  overviewStats.candidateMetrics = candidateMetricRes?.total_count || 0
  overviewStats.candidateDimensions = candidateDimensionRes?.total_count || 0
  if (activeTab.value !== 'candidates') {
    candidatesPage.total = candidateTotal.value
  }
}

const search = async () => {
  if (!datasourceId.value) return
  loading.value = true
  try {
    if (activeTab.value === 'metrics') {
      await loadMetrics()
    } else if (activeTab.value === 'dimensions') {
      await loadDimensions()
    } else {
      await loadCandidates()
    }
  } finally {
    loading.value = false
  }
}

const loadMetrics = async () => {
  const res = await semanticApi.metricPage(metricsPage.currentPage, metricsPage.pageSize, buildPageParams())
  metrics.value = res?.data || []
  metricsPage.total = res?.total_count || 0
}

const loadDimensions = async () => {
  const res = await semanticApi.dimensionPage(
    dimensionsPage.currentPage,
    dimensionsPage.pageSize,
    buildPageParams()
  )
  dimensions.value = res?.data || []
  dimensionsPage.total = res?.total_count || 0
}

const loadCandidates = async () => {
  const [metricRes, dimensionRes] = await Promise.all([
    semanticApi.metricPage(1, 100, buildPageParams('CANDIDATE')),
    semanticApi.dimensionPage(1, 100, buildPageParams('CANDIDATE')),
  ])
  const metricCandidates = (metricRes?.data || []).map((item: any) => ({
    ...item,
    asset_type: 'METRIC',
  }))
  const dimensionCandidates = (dimensionRes?.data || []).map((item: any) => ({
    ...item,
    asset_type: 'DIMENSION',
  }))
  candidateRows.value = [...metricCandidates, ...dimensionCandidates].sort((a, b) => {
    return new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
  })
  candidatesPage.total = (metricRes?.total_count || 0) + (dimensionRes?.total_count || 0)
}

const handleTabChange = () => {
  filterForm.status = activeTab.value === 'candidates' ? 'CANDIDATE' : ''
  currentPageState.value.currentPage = 1
  search()
}

const resetForm = () => {
  Object.assign(assetForm, {
    ...emptyForm,
    datasource_id: datasourceId.value,
    status: 'CANDIDATE',
  })
  fieldOptions.value = []
  validateResult.value = null
}

const openCreateDrawer = (assetType: AssetType) => {
  resetForm()
  drawerMode.value = 'create'
  drawerAssetType.value = assetType
  drawerVisible.value = true
}

const openEditDrawer = async (row: any, assetType: AssetType = row.asset_type || drawerAssetType.value) => {
  resetForm()
  drawerMode.value = 'edit'
  drawerAssetType.value = assetType
  Object.assign(assetForm, {
    ...emptyForm,
    ...row,
    datasource_id: row.datasource_id || datasourceId.value,
    table_id: row.table_id || '',
    field_id: row.field_id || '',
    aliases_text: (row.aliases || []).join('，'),
    related_dimension_ids_text: (row.related_dimension_ids || []).join(','),
    time_granularities_text: (row.time_granularities || []).join(','),
    default_values_text: (row.default_values || []).join('，'),
    filter_sql: row.filter_sql || '',
    description: row.description || '',
  })
  await loadFields(assetForm.table_id)
  drawerVisible.value = true
}

const handleTableChange = async () => {
  assetForm.field_id = ''
  await loadFields(assetForm.table_id)
}

const handleFieldChange = () => {
  const field = fieldOptions.value.find((item) => `${item.id}` === `${assetForm.field_id}`)
  if (!field) return
  const fieldName = field.field_name || field.fieldName
  if (!assetForm.name) assetForm.name = fieldName
  if (!assetForm.display_name) assetForm.display_name = field.field_comment || field.custom_comment || fieldName
  if (!assetForm.expr) assetForm.expr = fieldName
  if (!assetForm.data_type) assetForm.data_type = field.field_type || field.fieldType || ''
}

const splitText = (value: string) => {
  return value
    ? value
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
    : []
}

const buildAssetPayload = () => {
  const base: any = {
    datasource_id: datasourceId.value,
    table_id: assetForm.table_id || null,
    field_id: assetForm.field_id || null,
    name: assetForm.name,
    display_name: assetForm.display_name,
    aliases: splitText(assetForm.aliases_text),
    description: assetForm.description || null,
    expr: assetForm.expr,
    data_type: assetForm.data_type || null,
    owner_id: assetForm.owner_id || null,
  }
  if (drawerAssetType.value === 'METRIC') {
    return {
      ...base,
      define_type: assetForm.define_type,
      default_agg: assetForm.default_agg,
      filter_sql: assetForm.filter_sql || null,
      data_format: assetForm.data_format || null,
      default_time_dimension_id: assetForm.default_time_dimension_id || null,
      related_dimension_ids: splitText(assetForm.related_dimension_ids_text).map(Number).filter(Boolean),
      status: assetForm.status,
    }
  }
  return {
    ...base,
    dimension_type: assetForm.dimension_type,
    semantic_type: assetForm.semantic_type,
    time_granularities: splitText(assetForm.time_granularities_text),
    default_values: splitText(assetForm.default_values_text),
    status: assetForm.status,
  }
}

const validateExpression = async (row?: any) => {
  const target = row || assetForm
  const assetType: AssetType = row?.asset_type || drawerAssetType.value
  if (!target.table_id || !target.expr) {
    ElMessage.warning('请选择来源表并填写表达式')
    return
  }
  validateLoading.value = true
  try {
    const payload: ValidateExpressionPayload = {
      asset_type: assetType,
      table_id: target.table_id,
      expr: target.expr,
      default_agg: target.default_agg || 'SUM',
      filter_sql: target.filter_sql || undefined,
    }
    const result = await semanticApi.validate(datasourceId.value, payload)
    if (row) {
      candidateValidateResult[`${assetType}-${row.id}`] = result
    } else {
      validateResult.value = result
    }
    if (result.valid) {
      ElMessage.success('表达式校验通过')
    } else {
      ElMessage.error(result.error || '表达式校验失败')
    }
  } finally {
    validateLoading.value = false
  }
}

const saveAsset = async () => {
  if (!assetForm.name || !assetForm.display_name || !assetForm.expr) {
    ElMessage.warning('请填写技术名、展示名和表达式')
    return
  }
  saveLoading.value = true
  try {
    const payload = buildAssetPayload()
    if (drawerAssetType.value === 'METRIC') {
      if (drawerMode.value === 'create') {
        await semanticApi.metricCreate(payload)
      } else {
        await semanticApi.metricUpdate(assetForm.id, payload)
      }
    } else if (drawerMode.value === 'create') {
      await semanticApi.dimensionCreate(payload)
    } else {
      await semanticApi.dimensionUpdate(assetForm.id, payload)
    }
    ElMessage.success('保存成功')
    drawerVisible.value = false
    await loadOverviewStats()
    await search()
  } finally {
    saveLoading.value = false
  }
}

const approveAsset = async (row?: any) => {
  const target = row || assetForm
  const assetType: AssetType = row?.asset_type || drawerAssetType.value
  if (assetType === 'METRIC') {
    await semanticApi.metricApprove(target.id)
  } else {
    await semanticApi.dimensionApprove(target.id)
  }
  ElMessage.success('审核通过')
  drawerVisible.value = false
  await loadOverviewStats()
  await search()
}

const disableAsset = async (row?: any) => {
  const target = row || assetForm
  const assetType: AssetType = row?.asset_type || drawerAssetType.value
  await ElMessageBox.confirm(`确认禁用 ${target.display_name || target.name}？`, {
    confirmButtonText: '禁用',
    cancelButtonText: '取消',
    confirmButtonType: 'danger',
    customClass: 'confirm-no_icon',
    autofocus: false,
  })
  if (assetType === 'METRIC') {
    await semanticApi.metricDisable(target.id, 'disabled from semantic manager')
  } else {
    await semanticApi.dimensionDisable(target.id, 'disabled from semantic manager')
  }
  ElMessage.success('已禁用')
  drawerVisible.value = false
  await loadOverviewStats()
  await search()
}

const rebuildEmbedding = async (row?: any) => {
  const target = row || assetForm
  const assetType: AssetType = row?.asset_type || drawerAssetType.value
  if (assetType === 'METRIC') {
    await semanticApi.metricEmbedding(target.id)
  } else {
    await semanticApi.dimensionEmbedding(target.id)
  }
  ElMessage.success('已提交重建')
}

const initializeCandidates = async () => {
  if (!datasourceId.value) return
  initLoading.value = true
  try {
    const result = await semanticApi.initialize(datasourceId.value, {
      overwrite_candidate: false,
      table_ids: filterForm.table_id ? [Number(filterForm.table_id)] : [],
      dry_run: false,
    })
    ElMessage.success(
      `初始化完成：新增指标 ${result.created_metrics}，新增维度 ${result.created_dimensions}，跳过 ${result.skipped}`
    )
    activeTab.value = 'candidates'
    await loadOverviewStats()
    await search()
  } finally {
    initLoading.value = false
  }
}

const handleSelectionChange = (rows: any[]) => {
  selectedCandidates.value = rows
}

const batchApprove = async () => {
  if (!selectedCandidates.value.length) return
  await Promise.all(selectedCandidates.value.map((row) => approveCandidate(row)))
  ElMessage.success('批量审核完成')
  selectedCandidates.value = []
  await loadOverviewStats()
  await search()
}

const approveCandidate = async (row: any) => {
  if (row.asset_type === 'METRIC') return semanticApi.metricApprove(row.id)
  return semanticApi.dimensionApprove(row.id)
}

const batchDisable = async () => {
  if (!selectedCandidates.value.length) return
  await ElMessageBox.confirm(`确认禁用已选择的 ${selectedCandidates.value.length} 个候选资产？`, {
    confirmButtonText: '禁用',
    cancelButtonText: '取消',
    confirmButtonType: 'danger',
    customClass: 'confirm-no_icon',
    autofocus: false,
  })
  await Promise.all(
    selectedCandidates.value.map((row) =>
      row.asset_type === 'METRIC'
        ? semanticApi.metricDisable(row.id, 'batch disabled from semantic manager')
        : semanticApi.dimensionDisable(row.id, 'batch disabled from semantic manager')
    )
  )
  ElMessage.success('批量禁用完成')
  selectedCandidates.value = []
  await loadOverviewStats()
  await search()
}

const batchValidate = async () => {
  if (!selectedCandidates.value.length) return
  for (const row of selectedCandidates.value) {
    await validateExpression(row)
  }
}

const handlePageChange = (page: number) => {
  currentPageState.value.currentPage = page
  search()
}

const handlePageSizeChange = (size: number) => {
  currentPageState.value.pageSize = size
  currentPageState.value.currentPage = 1
  search()
}

const statusType = (status: AssetStatus) => {
  if (status === 'APPROVED') return 'success'
  if (status === 'CANDIDATE') return 'warning'
  if (status === 'DISABLED') return 'info'
  return ''
}

const statusText = (status: AssetStatus) => {
  return {
    APPROVED: '已审核',
    CANDIDATE: '候选',
    DISABLED: '已禁用',
    DEPRECATED: '已废弃',
  }[status]
}

const formatDate = (value?: string) => {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

const candidateRowKey = (row: any) => `${row.asset_type}-${row.id}`
</script>

<template>
  <div class="semantic-page">
    <div class="semantic-header">
      <div>
        <div class="breadcrumb">工作区 / 指标维度语义层</div>
        <h2>指标维度</h2>
        <p>治理已审核业务指标和维度，供智能问答召回并注入 SQL Prompt。</p>
      </div>
      <div class="header-actions">
        <el-select
          v-model="datasourceId"
          class="datasource-select"
          placeholder="选择数据源"
          @change="handleDatasourceChange"
        >
          <el-option
            v-for="item in datasourceOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
        <el-button :icon="Refresh" :loading="initLoading" @click="initializeCandidates">
          初始化候选
        </el-button>
        <el-button type="primary" :icon="Plus" @click="openCreateDrawer('METRIC')">
          新建指标
        </el-button>
        <el-button type="primary" plain :icon="Plus" @click="openCreateDrawer('DIMENSION')">
          新建维度
        </el-button>
      </div>
    </div>

    <template v-if="datasourceId">
      <div class="overview">
        <div class="ds-card">
          <div class="ds-logo">{{ (selectedDatasource?.type_name || selectedDatasource?.type || 'DS').slice(0, 2) }}</div>
          <div>
            <h3>{{ selectedDatasource?.name }}</h3>
            <p>{{ selectedDatasource?.description || '当前数据源可通过字段同步初始化指标维度候选。' }}</p>
            <span>{{ selectedDatasource?.num || '-' }} 张表字段范围</span>
          </div>
          <el-tag type="success" effect="light">连接成功</el-tag>
        </div>
        <div class="stat-card">
          <span>已审核指标</span>
          <strong>{{ overviewStats.approvedMetrics }}</strong>
          <small>可用于问答</small>
        </div>
        <div class="stat-card">
          <span>已审核维度</span>
          <strong>{{ overviewStats.approvedDimensions }}</strong>
          <small>分组/筛选</small>
        </div>
        <div class="stat-card">
          <span>待审核候选</span>
          <strong>{{ candidateTotal }}</strong>
          <small>来自字段初始化</small>
        </div>
      </div>

      <div class="table-panel">
        <div class="tabs-row">
          <el-tabs v-model="activeTab" @tab-change="handleTabChange">
            <el-tab-pane :label="`指标 ${metricsPage.total}`" name="metrics" />
            <el-tab-pane :label="`维度 ${dimensionsPage.total}`" name="dimensions" />
            <el-tab-pane :label="`候选 ${candidateTotal}`" name="candidates" />
          </el-tabs>
          <div v-if="activeTab === 'candidates' && selectedCandidates.length" class="batch-bar">
            <span>已选择 {{ selectedCandidates.length }} 项</span>
            <el-button :icon="MagicStick" @click="batchValidate">批量校验</el-button>
            <el-button type="success" :icon="Check" @click="batchApprove">批量审核</el-button>
            <el-button type="danger" plain :icon="Close" @click="batchDisable">批量禁用</el-button>
          </div>
        </div>

        <div class="filter-row">
          <el-input
            v-model="filterForm.keyword"
            class="search-input"
            clearable
            placeholder="搜索名称、别名、表达式"
            :prefix-icon="Search"
            @keyup.enter="search"
            @clear="search"
          />
          <el-select v-model="filterForm.table_id" clearable placeholder="来源表：全部" @change="search">
            <el-option
              v-for="item in tableOptions"
              :key="item.id"
              :label="item.table_name"
              :value="item.id"
            />
          </el-select>
          <el-select
            v-if="activeTab !== 'candidates'"
            v-model="filterForm.status"
            clearable
            placeholder="状态：全部"
            @change="search"
          >
            <el-option label="候选" value="CANDIDATE" />
            <el-option label="已审核" value="APPROVED" />
            <el-option label="已禁用" value="DISABLED" />
          </el-select>
          <el-button :icon="Search" @click="search">查询</el-button>
        </div>

        <el-table
          v-if="activeTab === 'metrics'"
          v-loading="loading"
          :data="metrics"
          height="calc(100vh - 420px)"
          row-key="id"
        >
          <el-table-column label="指标" min-width="210">
            <template #default="{ row }">
              <div class="asset-name">
                <span class="asset-icon metric">M</span>
                <div>
                  <strong>{{ row.display_name }}</strong>
                  <p>{{ row.name }}</p>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="描述" min-width="220">
            <template #default="{ row }">
              <span class="description-cell">{{ row.description || '未填写描述' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="expr" label="表达式" min-width="180" />
          <el-table-column label="别名" min-width="150">
            <template #default="{ row }">{{ (row.aliases || []).join('、') || '-' }}</template>
          </el-table-column>
          <el-table-column label="来源" min-width="160">
            <template #default="{ row }">
              <strong>{{ tableNameById[`${row.table_id}`] || row.table_id || 'derived' }}</strong>
              <p class="muted">{{ fieldNameById[`${row.field_id}`] || row.field_id || row.define_type }}</p>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" effect="light">{{ statusText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="160">
            <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" fixed="right" width="138">
            <template #default="{ row }">
              <el-button :icon="Edit" text type="primary" @click="openEditDrawer(row, 'METRIC')" />
              <el-dropdown trigger="click">
                <el-button :icon="MoreFilled" text type="primary" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item @click="approveAsset(row)">审核</el-dropdown-item>
                    <el-dropdown-item @click="rebuildEmbedding(row)">重建 embedding</el-dropdown-item>
                    <el-dropdown-item divided @click="disableAsset(row)">禁用</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </el-table-column>
        </el-table>

        <el-table
          v-if="activeTab === 'dimensions'"
          v-loading="loading"
          :data="dimensions"
          height="calc(100vh - 420px)"
          row-key="id"
        >
          <el-table-column label="维度" min-width="210">
            <template #default="{ row }">
              <div class="asset-name">
                <span class="asset-icon dimension">D</span>
                <div>
                  <strong>{{ row.display_name }}</strong>
                  <p>{{ row.name }}</p>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="描述" min-width="220">
            <template #default="{ row }">
              <span class="description-cell">{{ row.description || '未填写描述' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="230">
            <template #default="{ row }">
              <div class="type-tags">
                <el-tag type="info" effect="light">{{ row.dimension_type }}</el-tag>
                <el-tag effect="plain">{{ row.semantic_type }}</el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="expr" label="表达式" min-width="160" />
          <el-table-column label="别名/维值" min-width="180">
            <template #default="{ row }">
              <span>{{ (row.aliases || []).join('、') || '-' }}</span>
              <p class="muted">{{ (row.default_values || []).join('、') }}</p>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" effect="light">{{ statusText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="160">
            <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" fixed="right" width="138">
            <template #default="{ row }">
              <el-button :icon="Edit" text type="primary" @click="openEditDrawer(row, 'DIMENSION')" />
              <el-dropdown trigger="click">
                <el-button :icon="MoreFilled" text type="primary" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item @click="approveAsset(row)">审核</el-dropdown-item>
                    <el-dropdown-item @click="rebuildEmbedding(row)">重建 embedding</el-dropdown-item>
                    <el-dropdown-item divided @click="disableAsset(row)">禁用</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </el-table-column>
        </el-table>

        <el-table
          v-if="activeTab === 'candidates'"
          v-loading="loading"
          :data="pagedCandidates"
          height="calc(100vh - 420px)"
          :row-key="candidateRowKey"
          @selection-change="handleSelectionChange"
        >
          <el-table-column type="selection" width="48" />
          <el-table-column label="候选资产" min-width="210">
            <template #default="{ row }">
              <div class="asset-name">
                <span :class="['asset-icon', row.asset_type === 'METRIC' ? 'metric' : 'dimension']">
                  {{ row.asset_type === 'METRIC' ? 'M' : 'D' }}
                </span>
                <div>
                  <strong>{{ row.display_name }}</strong>
                  <p>{{ row.name }}</p>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="描述" min-width="220">
            <template #default="{ row }">
              <span class="description-cell">{{ row.description || '字段规则推断' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="建议类型" width="130">
            <template #default="{ row }">
              <el-tag :type="row.asset_type === 'METRIC' ? 'primary' : 'info'" effect="light">
                {{ row.asset_type === 'METRIC' ? '指标' : '维度' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源字段" min-width="160">
            <template #default="{ row }">
              <strong>{{ tableNameById[`${row.table_id}`] || row.table_id || '-' }}</strong>
              <p class="muted">{{ row.expr }}</p>
            </template>
          </el-table-column>
          <el-table-column label="推荐口径" min-width="220">
            <template #default="{ row }">
              <code>{{ row.asset_type === 'METRIC' ? `${row.default_agg}(${row.expr})` : row.expr }}</code>
              <p class="muted">
                {{ row.asset_type === 'METRIC' ? row.data_format || row.define_type : row.semantic_type }}
              </p>
            </template>
          </el-table-column>
          <el-table-column label="校验" min-width="160">
            <template #default="{ row }">
              <template v-if="candidateValidateResult[`${row.asset_type}-${row.id}`]">
                <el-tag
                  :type="candidateValidateResult[`${row.asset_type}-${row.id}`].valid ? 'success' : 'danger'"
                  effect="light"
                >
                  {{ candidateValidateResult[`${row.asset_type}-${row.id}`].valid ? '通过' : '失败' }}
                </el-tag>
                <p class="muted">{{ candidateValidateResult[`${row.asset_type}-${row.id}`].error }}</p>
              </template>
              <span v-else class="muted">未校验</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" fixed="right" width="240">
            <template #default="{ row }">
              <div class="candidate-actions">
                <el-button class="candidate-action" size="small" @click="validateExpression(row)">
                  校验
                </el-button>
                <el-button class="candidate-action" size="small" type="success" @click="approveAsset(row)">
                  审核
                </el-button>
                <el-button class="candidate-action edit" size="small" text @click="openEditDrawer(row)">
                  编辑
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-container">
          <el-pagination
            v-model:current-page="currentPageState.currentPage"
            v-model:page-size="currentPageState.pageSize"
            :page-sizes="[10, 20, 50]"
            :total="currentPageState.total"
            layout="total, sizes, prev, pager, next"
            @current-change="handlePageChange"
            @size-change="handlePageSizeChange"
          />
        </div>
      </div>
    </template>

    <div v-else class="empty-state">
      <h3>暂无数据源</h3>
      <p>创建并同步数据源后，可以在这里初始化指标维度候选。</p>
    </div>

    <el-drawer
      v-model="drawerVisible"
      :title="`${drawerMode === 'create' ? '新建' : '编辑'}${drawerAssetType === 'METRIC' ? '指标' : '维度'}`"
      size="420px"
      class="semantic-drawer"
    >
      <el-form label-position="top">
        <div class="drawer-status">
          <span>{{ assetForm.name || '未命名资产' }}</span>
          <el-tag :type="statusType(assetForm.status)" effect="light">{{ statusText(assetForm.status) }}</el-tag>
        </div>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="展示名">
              <el-input v-model="assetForm.display_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="技术名">
              <el-input v-model="assetForm.name" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="别名">
          <el-input v-model="assetForm.aliases_text" placeholder="GMV，收入，销售额" />
        </el-form-item>
        <el-form-item label="业务口径描述">
          <el-input v-model="assetForm.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="来源表">
              <el-select v-model="assetForm.table_id" clearable @change="handleTableChange">
                <el-option
                  v-for="item in tableOptions"
                  :key="item.id"
                  :label="item.table_name"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="来源字段">
              <el-select v-model="assetForm.field_id" clearable @change="handleFieldChange">
                <el-option
                  v-for="item in fieldOptions"
                  :key="item.id"
                  :label="item.field_name"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <template v-if="drawerAssetType === 'METRIC'">
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="定义方式">
                <el-select v-model="assetForm.define_type">
                  <el-option label="MEASURE" value="MEASURE" />
                  <el-option label="DERIVED" value="DERIVED" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="默认聚合">
                <el-select v-model="assetForm.default_agg">
                  <el-option label="SUM" value="SUM" />
                  <el-option label="AVG" value="AVG" />
                  <el-option label="COUNT" value="COUNT" />
                  <el-option label="COUNT_DISTINCT" value="COUNT_DISTINCT" />
                  <el-option label="MAX" value="MAX" />
                  <el-option label="MIN" value="MIN" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
        </template>
        <template v-else>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="维度类型">
                <el-select v-model="assetForm.dimension_type">
                  <el-option label="CATEGORY" value="CATEGORY" />
                  <el-option label="TIME" value="TIME" />
                  <el-option label="IDENTIFIER" value="IDENTIFIER" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="语义类型">
                <el-select v-model="assetForm.semantic_type">
                  <el-option label="UNKNOWN" value="UNKNOWN" />
                  <el-option label="DATE" value="DATE" />
                  <el-option label="DATETIME" value="DATETIME" />
                  <el-option label="REGION" value="REGION" />
                  <el-option label="CHANNEL" value="CHANNEL" />
                  <el-option label="CUSTOMER" value="CUSTOMER" />
                  <el-option label="PRODUCT" value="PRODUCT" />
                  <el-option label="USER" value="USER" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
        </template>
        <el-form-item label="表达式">
          <el-input v-model="assetForm.expr" type="textarea" :rows="4" class="code-input" />
        </el-form-item>
        <el-form-item v-if="drawerAssetType === 'METRIC'" label="过滤条件">
          <el-input v-model="assetForm.filter_sql" placeholder="status = 'paid'" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="数据类型">
              <el-input v-model="assetForm.data_type" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="drawerAssetType === 'METRIC' ? '数据格式' : '默认维值'">
              <el-input
                v-if="drawerAssetType === 'METRIC'"
                v-model="assetForm.data_format"
                placeholder="currency / percent"
              />
              <el-input v-else v-model="assetForm.default_values_text" placeholder="新老客，客群" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item v-if="drawerAssetType === 'DIMENSION'" label="时间粒度">
          <el-input v-model="assetForm.time_granularities_text" placeholder="day,month,year" />
        </el-form-item>
        <el-form-item label="表达式校验预览">
          <div class="validate-preview">
            <pre v-if="validateResult?.check_sql">{{ validateResult.check_sql }}</pre>
            <span v-else class="muted">点击校验表达式后显示解析 SQL。</span>
            <div v-if="validateResult" class="validate-result">
              <el-tag :type="validateResult.valid ? 'success' : 'danger'" effect="light">
                {{ validateResult.valid ? '校验通过' : '校验失败' }}
              </el-tag>
              <span>{{ validateResult.error || validateResult.warnings?.join('；') }}</span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="drawer-footer">
          <el-button @click="drawerVisible = false">取消</el-button>
          <el-button :loading="validateLoading" @click="validateExpression()">校验表达式</el-button>
          <el-dropdown v-if="assetForm.id" trigger="click">
            <el-button :icon="MoreFilled">更多</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="rebuildEmbedding()">重建 embedding</el-dropdown-item>
                <el-dropdown-item @click="approveAsset()">审核</el-dropdown-item>
                <el-dropdown-item class="drawer-danger-action" divided @click="disableAsset()">
                  禁用
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button type="primary" :loading="saveLoading" @click="saveAsset">保存</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped lang="less">
.semantic-page {
  min-height: 100%;
  padding: 24px;
  background: #f4f7fb;
  color: #1f2633;
}

.semantic-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 18px;

  h2 {
    margin: 14px 0 6px;
    font-size: 24px;
    font-weight: 700;
  }

  p,
  .breadcrumb {
    margin: 0;
    color: #6b7280;
    font-size: 13px;
  }
}

.header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 520px;
}

.datasource-select {
  width: 220px;
}

.overview {
  display: grid;
  grid-template-columns: minmax(360px, 1fr) repeat(3, minmax(150px, 220px));
  gap: 12px;
  margin-bottom: 16px;
}

.ds-card,
.stat-card,
.table-panel,
.empty-state {
  border: 1px solid #e6ebf2;
  background: #fff;
  border-radius: 8px;
}

.ds-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;

  h3 {
    margin: 0 0 6px;
    font-size: 16px;
  }

  p,
  span {
    margin: 0;
    color: #6b7280;
    font-size: 13px;
  }

  .ed-tag {
    margin-left: auto;
  }
}

.ds-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  background: #e8f8ef;
  color: #149452;
  font-weight: 700;
}

.stat-card {
  padding: 16px;

  span,
  small {
    display: block;
    color: #6b7280;
    font-size: 13px;
  }

  strong {
    display: block;
    margin: 8px 0 4px;
    font-size: 28px;
    line-height: 1;
  }
}

.table-panel {
  overflow: hidden;
}

.tabs-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid #e6ebf2;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #374151;
  font-size: 13px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  background: #fff;

  .search-input {
    width: 280px;
  }

  .ed-select {
    width: 180px;
  }
}

.asset-name {
  display: flex;
  align-items: center;
  gap: 12px;

  strong {
    display: block;
    font-weight: 700;
  }

  p {
    margin: 4px 0 0;
    color: #8b95a5;
    font-size: 13px;
  }
}

.asset-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  font-weight: 700;
}

.asset-icon.metric {
  background: #eaf1ff;
  color: #2f6df6;
}

.asset-icon.dimension {
  background: #e8faf7;
  color: #0f9c95;
}

.muted {
  margin: 4px 0 0;
  color: #8b95a5;
  font-size: 13px;
}

.description-cell {
  display: -webkit-box;
  overflow: hidden;
  color: #6b7280;
  line-height: 1.5;
  word-break: break-word;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.type-tags {
  display: grid;
  grid-template-columns: 92px 112px;
  gap: 8px;
  align-items: center;
  width: 212px;
}

.type-tags :deep(.ed-tag) {
  justify-content: center;
  width: 100%;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 204px;
  white-space: nowrap;
}

.candidate-actions :deep(.candidate-action.ed-button) {
  flex: 0 0 68px;
  width: 68px;
  min-width: 0 !important;
  height: 32px;
  margin-left: 0 !important;
  padding: 0 10px;
  justify-content: center;
}

.candidate-actions :deep(.candidate-action.edit.ed-button) {
  flex-basis: 44px;
  width: 44px;
  padding: 0;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  padding: 16px;
  border-top: 1px solid #edf1f6;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 420px;

  h3 {
    margin: 0 0 8px;
  }

  p {
    margin: 0;
    color: #6b7280;
  }
}

.drawer-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  font-weight: 700;
}

.validate-preview {
  width: 100%;
  border: 1px solid #e6ebf2;
  border-radius: 8px;
  padding: 12px;
  background: #f8fafc;

  pre {
    margin: 0;
    padding: 12px;
    border-radius: 6px;
    background: #111827;
    color: #fff;
    white-space: pre-wrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
  }
}

.validate-result {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: #6b7280;
  font-size: 13px;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.drawer-danger-action {
  color: #f56c6c;
}

:deep(.semantic-drawer .ed-drawer__body) {
  padding-bottom: 12px;
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
  .semantic-header,
  .header-actions {
    display: block;
    min-width: 0;
  }

  .header-actions {
    margin-top: 14px;
  }

  .overview {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
