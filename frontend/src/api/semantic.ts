import { request } from '@/utils/request'

export interface SchemaMapPayload {
  query_text: string
  dataset_ids: Array<number | string>
}

export interface DimensionHierarchyPayload {
  domain_id: number
  name: string
  biz_name: string
  description?: string
  hierarchy_type?: 'FIXED_LEVEL'
  levels: Array<{ logical_dimension_id: number; level_order: number }>
}

export interface MetricRelationshipPayload {
  domain_id: number
  target_metric_id: number
  driver_metric_id: number
  relationship_type: 'FORMULA_COMPONENT' | 'CERTIFIED_DRIVER' | 'GOVERNED_ANALYSIS_RELATION'
  validation_method: 'SAME_DIRECTION' | 'OPPOSITE_DIRECTION' | 'FORMULA_RECONCILIATION'
  expected_direction: 'POSITIVE' | 'NEGATIVE' | 'UNKNOWN'
  supported_time_roles: string[]
  logical_dimension_ids: number[]
  relation_path: number[]
}

export interface MetricDimensionCapabilityPayload {
  metric_id: number
  logical_dimension_id: number
  usages: Array<'GROUP_BY' | 'FILTER' | 'DETAIL' | 'CONTRIBUTION'>
  binding_strategy: 'SAME_MODEL' | 'RELATION_PATH'
  relation_path: number[]
  target_model_id: number
  physical_dimension_id?: number
  aggregation_safety: 'SAFE' | 'PRE_AGGREGATE_REQUIRED' | 'FORBIDDEN'
  pre_aggregation_grain: string[]
  time_alignment_policy: 'SAME_TIME' | 'AS_OF' | 'NONE'
  contribution_tolerance: number
  ext?: Record<string, unknown>
}

export const semanticApi = {
  datasourceList: () => request.get('/semantic/datasources'),
  datasourceTables: (id: number | string) => request.get(`/semantic/datasources/${id}/tables`),
  datasourceColumns: (id: number | string, tableName: string) =>
    request.get(`/semantic/datasources/${id}/tables/${encodeURIComponent(tableName)}/columns`),

  domainList: () => request.get('/semantic/domains'),
  domainCreate: (data: any) => request.post('/semantic/domains', data),
  domainUpdate: (id: number | string, data: any) => request.put(`/semantic/domains/${id}`, data),
  domainDelete: (id: number | string) => request.delete(`/semantic/domains/${id}`),

  modelList: (params?: any) => request.get('/semantic/models', { params }),
  modelCreate: (data: any) => request.post('/semantic/models', data),
  modelUpdate: (id: number | string, data: any) => request.put(`/semantic/models/${id}`, data),
  modelDelete: (id: number | string) => request.delete(`/semantic/models/${id}`),
  modelBuildSchema: (data: any) => request.post('/semantic/models/build-schema', data),
  modelCreateWithAssets: (data: any) => request.post('/semantic/models/create-with-assets', data),

  metricList: (params?: any) => request.get('/semantic/metrics', { params }),
  metricCreate: (data: any) => request.post('/semantic/metrics', data),
  metricBatchCreateFromMeasures: (data: any) => request.post('/semantic/metrics/batch-create-from-measures', data),
  metricUpdate: (id: number | string, data: any) => request.put(`/semantic/metrics/${id}`, data),
  metricDelete: (id: number | string) => request.delete(`/semantic/metrics/${id}`),

  dimensionList: (params?: any) => request.get('/semantic/dimensions', { params }),
  dimensionCreate: (data: any) => request.post('/semantic/dimensions', data),
  dimensionUpdate: (id: number | string, data: any) => request.put(`/semantic/dimensions/${id}`, data),
  dimensionDelete: (id: number | string) => request.delete(`/semantic/dimensions/${id}`),

  datasetList: (params?: any) => request.get('/semantic/datasets', { params }),
  datasetCreate: (data: any) => request.post('/semantic/datasets', data),
  datasetUpdate: (id: number | string, data: any) => request.put(`/semantic/datasets/${id}`, data),
  datasetDelete: (id: number | string) => request.delete(`/semantic/datasets/${id}`),
  datasetSchema: (id: number | string) => request.get(`/semantic/datasets/${id}/schema`),
  datasetContractReport: (id: number | string) => request.get(`/semantic/datasets/${id}/contract-report`),
  datasetPublishContract: (id: number | string) => request.post(`/semantic/datasets/${id}/publish-contract`),
  datasetAnalysisCapabilities: (id: number | string) => request.get(`/semantic/datasets/${id}/analysis-capabilities`),
  datasetContractReferences: (id: number | string) => request.get(`/semantic/datasets/${id}/contract-references`),
  datasetContractImpact: (id: number | string) => request.get(`/semantic/datasets/${id}/impact-analysis`),
  datasetContractVersions: (id: number | string) => request.get(`/semantic/datasets/${id}/contract-versions`),

  businessEntityList: (params?: any) => request.get('/semantic/business-entities', { params }),
  businessEntityCreate: (data: any) => request.post('/semantic/business-entities', data),
  businessEntityUpdate: (id: number | string, data: any) => request.put(`/semantic/business-entities/${id}`, data),
  businessEntityDelete: (id: number | string) => request.delete(`/semantic/business-entities/${id}`),
  logicalDimensionList: (params?: any) => request.get('/semantic/logical-dimensions', { params }),
  logicalDimensionCreate: (data: any) => request.post('/semantic/logical-dimensions', data),
  logicalDimensionUpdate: (id: number | string, data: any) => request.put(`/semantic/logical-dimensions/${id}`, data),
  logicalDimensionDelete: (id: number | string) => request.delete(`/semantic/logical-dimensions/${id}`),

  dimensionHierarchyList: (params?: any) => request.get('/semantic/dimension-hierarchies', { params }),
  dimensionHierarchyCreate: (data: DimensionHierarchyPayload) => request.post('/semantic/dimension-hierarchies', data),
  dimensionHierarchyUpdate: (id: number | string, data: DimensionHierarchyPayload) => request.put(`/semantic/dimension-hierarchies/${id}`, data),
  dimensionHierarchyDelete: (id: number | string) => request.delete(`/semantic/dimension-hierarchies/${id}`),
  metricRelationshipList: (params?: any) => request.get('/semantic/metric-relationships', { params }),
  metricRelationshipCreate: (data: MetricRelationshipPayload) => request.post('/semantic/metric-relationships', data),
  metricRelationshipUpdate: (id: number | string, data: MetricRelationshipPayload) => request.put(`/semantic/metric-relationships/${id}`, data),
  metricRelationshipDelete: (id: number | string) => request.delete(`/semantic/metric-relationships/${id}`),
  metricDimensionCapabilityList: (params?: any) => request.get('/semantic/metric-dimension-capabilities', { params }),
  metricDimensionCapabilityCreate: (data: MetricDimensionCapabilityPayload) => request.post('/semantic/metric-dimension-capabilities', data),
  metricDimensionCapabilityUpdate: (id: number | string, data: MetricDimensionCapabilityPayload) => request.put(`/semantic/metric-dimension-capabilities/${id}`, data),
  metricDimensionCapabilityDelete: (id: number | string) => request.delete(`/semantic/metric-dimension-capabilities/${id}`),

  termList: (params?: any) => request.get('/semantic/terms', { params }),
  termCreate: (data: any) => request.post('/semantic/terms', data),
  termUpdate: (id: number | string, data: any) => request.put(`/semantic/terms/${id}`, data),
  termDelete: (id: number | string) => request.delete(`/semantic/terms/${id}`),

  schemaMap: (data: SchemaMapPayload) => request.post('/semantic/schema/map', data),
  datasetIndexRebuild: (datasetId: number | string) =>
    request.post(`/semantic/datasets/${datasetId}/index/rebuild`),
}
