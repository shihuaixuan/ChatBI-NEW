import { request } from '@/utils/request'

export interface SchemaMapPayload {
  query_text: string
  dataset_ids: Array<number | string>
}

export const headlessApi = {
  datasourceList: () => request.get('/headless/datasources'),
  datasourceTables: (id: number | string) => request.get(`/headless/datasources/${id}/tables`),
  datasourceColumns: (id: number | string, tableName: string) =>
    request.get(`/headless/datasources/${id}/tables/${encodeURIComponent(tableName)}/columns`),

  domainList: () => request.get('/headless/domains'),
  domainCreate: (data: any) => request.post('/headless/domains', data),
  domainUpdate: (id: number | string, data: any) => request.put(`/headless/domains/${id}`, data),
  domainDelete: (id: number | string) => request.delete(`/headless/domains/${id}`),

  modelList: (params?: any) => request.get('/headless/models', { params }),
  modelCreate: (data: any) => request.post('/headless/models', data),
  modelUpdate: (id: number | string, data: any) => request.put(`/headless/models/${id}`, data),
  modelDelete: (id: number | string) => request.delete(`/headless/models/${id}`),
  modelBuildSchema: (data: any) => request.post('/headless/models/build-schema', data),
  modelCreateWithAssets: (data: any) => request.post('/headless/models/create-with-assets', data),

  metricList: (params?: any) => request.get('/headless/metrics', { params }),
  metricCreate: (data: any) => request.post('/headless/metrics', data),
  metricBatchCreateFromMeasures: (data: any) => request.post('/headless/metrics/batch-create-from-measures', data),
  metricUpdate: (id: number | string, data: any) => request.put(`/headless/metrics/${id}`, data),
  metricDelete: (id: number | string) => request.delete(`/headless/metrics/${id}`),

  dimensionList: (params?: any) => request.get('/headless/dimensions', { params }),
  dimensionCreate: (data: any) => request.post('/headless/dimensions', data),
  dimensionUpdate: (id: number | string, data: any) => request.put(`/headless/dimensions/${id}`, data),
  dimensionDelete: (id: number | string) => request.delete(`/headless/dimensions/${id}`),

  datasetList: (params?: any) => request.get('/headless/datasets', { params }),
  datasetCreate: (data: any) => request.post('/headless/datasets', data),
  datasetUpdate: (id: number | string, data: any) => request.put(`/headless/datasets/${id}`, data),
  datasetDelete: (id: number | string) => request.delete(`/headless/datasets/${id}`),
  datasetSchema: (id: number | string) => request.get(`/headless/datasets/${id}/schema`),

  termList: (params?: any) => request.get('/headless/terms', { params }),
  termCreate: (data: any) => request.post('/headless/terms', data),
  termUpdate: (id: number | string, data: any) => request.put(`/headless/terms/${id}`, data),
  termDelete: (id: number | string) => request.delete(`/headless/terms/${id}`),

  schemaMap: (data: SchemaMapPayload) => request.post('/headless/schema/map', data),
  knowledgeRebuild: (datasetId: number | string) =>
    request.post('/headless/knowledge/rebuild', null, { params: { dataset_id: datasetId } }),
  metricEmbeddingRebuild: (datasetId: number | string) =>
    request.post(`/headless/datasets/${datasetId}/metric-embeddings/rebuild`),
  metricEmbeddingList: (datasetId: number | string) =>
    request.get(`/headless/datasets/${datasetId}/metric-embeddings`),
}
