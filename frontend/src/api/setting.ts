import { request } from '@/utils/request'

export const settingsApi = {
  downloadError: (path: any) =>
    request.post(
      `/system/download-fail-info`,
      { file: path },
      {
        responseType: 'blob',
        requestOptions: { customError: true },
      }
    ),

  downloadTemplate: (url: any) =>
    request.get(url, {
      responseType: 'blob',
      requestOptions: { customError: true },
    }),
}
