import { request } from '@/utils/request'

export const modelApi = {
  queryAll: (keyword?: string) =>
    request.get('/system/aimodel', { params: keyword ? { keyword } : {} }),
  add: (data: any) => request.post('/system/aimodel', data),
  edit: (data: any) => request.put('/system/aimodel', data),
  delete: (id: number) => request.delete(`/system/aimodel/${id}`),
  query: (id: number) => request.get(`/system/aimodel/${id}`),
  setDefault: (id: number) => request.put(`/system/aimodel/default/${id}`),
  check: (data: any) => request.fetchStream('/system/aimodel/status', data),
}
