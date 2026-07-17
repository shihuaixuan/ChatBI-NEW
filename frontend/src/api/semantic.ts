import { request } from '@/utils/request'

export interface SchemaMapPayload {
  query_text: string
  dataset_ids: Array<number | string>
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

  termList: (params?: any) => request.get('/semantic/terms', { params }),
  termCreate: (data: any) => request.post('/semantic/terms', data),
  termUpdate: (id: number | string, data: any) => request.put(`/semantic/terms/${id}`, data),
  termDelete: (id: number | string) => request.delete(`/semantic/terms/${id}`),

  schemaMap: (data: SchemaMapPayload) => request.post('/semantic/schema/map', data),
  knowledgeRebuild: (datasetId: number | string) =>
    request.post('/semantic/knowledge/rebuild', null, { params: { dataset_id: datasetId } }),
}
