import { request } from '@/utils/request'

export interface SemanticPageParams {
  datasource_id: number | string
  keyword?: string
  status?: string
  table_id?: number | string
  owner_id?: number | string
}

export interface ValidateExpressionPayload {
  asset_type: 'METRIC' | 'DIMENSION'
  table_id: number | string
  expr: string
  default_agg?: string
  filter_sql?: string
}

export const semanticApi = {
  metricPage: (page: number, size: number, params: SemanticPageParams) =>
    request.get(`/semantic/metrics/page/${page}/${size}`, { params }),
  metricCreate: (data: any) => request.post('/semantic/metrics', data),
  metricUpdate: (id: number | string, data: any) => request.put(`/semantic/metrics/${id}`, data),
  metricApprove: (id: number | string) => request.post(`/semantic/metrics/${id}/approve`),
  metricDisable: (id: number | string, reason = '') =>
    request.post(`/semantic/metrics/${id}/disable`, { reason }),
  metricEmbedding: (id: number | string) => request.post(`/semantic/metrics/${id}/embedding`),

  dimensionPage: (page: number, size: number, params: SemanticPageParams) =>
    request.get(`/semantic/dimensions/page/${page}/${size}`, { params }),
  dimensionCreate: (data: any) => request.post('/semantic/dimensions', data),
  dimensionUpdate: (id: number | string, data: any) => request.put(`/semantic/dimensions/${id}`, data),
  dimensionApprove: (id: number | string) => request.post(`/semantic/dimensions/${id}/approve`),
  dimensionDisable: (id: number | string, reason = '') =>
    request.post(`/semantic/dimensions/${id}/disable`, { reason }),
  dimensionEmbedding: (id: number | string) => request.post(`/semantic/dimensions/${id}/embedding`),

  initialize: (datasourceId: number | string, data: any) =>
    request.post(`/semantic/datasources/${datasourceId}/initialize`, data),
  validate: (datasourceId: number | string, data: ValidateExpressionPayload) =>
    request.post(`/semantic/datasources/${datasourceId}/validate`, data),
  chatRecordAssets: (recordId: number | string) =>
    request.get(`/semantic/chat-records/${recordId}/assets`),
}
